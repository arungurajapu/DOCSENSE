import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  deleteDocument,
  getDocument,
  getHealth,
  isAbortError,
  listDocuments,
  reprocessDocument,
  uploadDocument,
} from "@/lib/api";
import { getApiBaseUrl, getMaxUploadSizeBytes, getMaxUploadSizeMb } from "@/lib/config";
import {
  formatFileSize,
  formatPageCount,
  formatUtcTimestamp,
  truncateSha256,
} from "@/lib/format";
import { validatePdfFile } from "@/lib/validation";
import type { DocumentResponse, HealthResponse } from "@/types/api";

const SAMPLE_DOC_ID = "550e8400-e29b-41d4-a716-446655440000";

function makeDoc(overrides: Partial<DocumentResponse> = {}): DocumentResponse {
  return {
    id: SAMPLE_DOC_ID,
    filename: "sample.pdf",
    content_type: "application/pdf",
    file_size_bytes: 2048,
    sha256_hash: "a".repeat(64),
    status: "ready",
    error_message: null,
    page_count: 3,
    chunk_count: 8,
    created_at: "2026-11-11T10:00:00Z",
    updated_at: "2026-11-11T10:01:00Z",
    ...overrides,
  };
}

describe("config, format, and validation utilities", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("resolves default and overridden environment configuration", () => {
    expect(getApiBaseUrl()).toBe("http://localhost:8000");
    expect(getMaxUploadSizeMb()).toBe(25);

    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:9000///");
    vi.stubEnv("NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB", "10");
    expect(getApiBaseUrl()).toBe("http://127.0.0.1:9000");
    expect(getMaxUploadSizeMb()).toBe(10);
    expect(getMaxUploadSizeBytes()).toBe(10 * 1024 * 1024);

    vi.stubEnv("NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB", "not-a-number");
    expect(getMaxUploadSizeMb()).toBe(25);
  });

  it("formats file sizes, page counts (0 vs null), hashes, and UTC timestamps", () => {
    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(1536)).toBe("1.5 KB");
    expect(formatFileSize(25 * 1024 * 1024)).toBe("25 MB");

    expect(formatPageCount(0)).toBe("0");
    expect(formatPageCount(null)).toBe("—");
    expect(formatPageCount(12)).toBe("12");

    expect(truncateSha256("abcdef1234567890", 6)).toBe("abcdef…");
    expect(formatUtcTimestamp("2026-11-11T05:06:07Z")).toBe(
      "2026-11-11 05:06:07 UTC",
    );
  });

  it("validates PDF suffix case-insensitively, non-empty size, and max size", () => {
    const maxBytes = 1024;
    const nonPdf = new File(["hello"], "notes.txt", { type: "text/plain" });
    expect(validatePdfFile(nonPdf, maxBytes)).toEqual({
      valid: false,
      error: 'Only PDF files (.pdf) are supported. Selected "notes.txt".',
    });

    const emptyPdf = new File([], "empty.PDF", { type: "application/pdf" });
    expect(validatePdfFile(emptyPdf, maxBytes)).toEqual({
      valid: false,
      error:
        'Selected file "empty.PDF" is empty (0 bytes). Please choose a non-empty PDF.',
    });

    const oversizedPdf = new File([new Uint8Array(1025)], "big.pdf", {
      type: "application/pdf",
    });
    expect(validatePdfFile(oversizedPdf, maxBytes).valid).toBe(false);

    const validUpperPdf = new File(["%PDF-1.4"], "REPORT.PDF", {
      type: "application/pdf",
    });
    expect(validatePdfFile(validUpperPdf, maxBytes)).toEqual({
      valid: true,
      error: null,
    });
  });
});

describe("API client contract behavior", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches health status and parses 503 ErrorResponse", async () => {
    const healthPayload: HealthResponse = {
      status: "ok",
      version: "0.1.0",
      database: "ok",
      vector_store: "ok",
      embedding_model_loaded: true,
      timestamp: "2026-11-11T10:00:00Z",
    };

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(healthPayload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: {
              code: "SERVICE_UNAVAILABLE",
              message: "Database connection failed.",
            },
          }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
      );

    await expect(getHealth()).resolves.toEqual(healthPayload);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/api/v1/health",
      expect.objectContaining({ method: "GET" }),
    );

    await expect(getHealth()).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      code: "SERVICE_UNAVAILABLE",
      message: "Database connection failed.",
    });
  });

  it("omits status query parameter when filter is 'all' and forwards exact enum otherwise", async () => {
    const listResponse = {
      items: [makeDoc()],
      total: 1,
      limit: 20,
      offset: 0,
    };

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => {
        return new Response(JSON.stringify(listResponse), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });

    await listDocuments({ status: "all", limit: 20, offset: 0 });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/api/v1/documents?limit=20&offset=0",
      expect.objectContaining({ method: "GET" }),
    );

    await listDocuments({ status: "processing", limit: 10, offset: 20 });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/api/v1/documents?status=processing&limit=10&offset=20",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("fetches single document, uploads multipart FormData, and reprocesses document", async () => {
    const doc = makeDoc({ status: "pending", page_count: null, chunk_count: 0 });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(doc), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(doc), { status: 202 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(doc), { status: 202 }),
      );

    await expect(getDocument(SAMPLE_DOC_ID)).resolves.toEqual(doc);

    const file = new File(["%PDF-1.4"], "upload.pdf", { type: "application/pdf" });
    await expect(uploadDocument(file)).resolves.toEqual(doc);
    const uploadInit = fetchMock.mock.calls[1]?.[1];
    expect(uploadInit?.method).toBe("POST");
    expect(uploadInit?.body).toBeInstanceOf(FormData);
    expect((uploadInit?.body as FormData).get("file")).toBe(file);

    await expect(reprocessDocument(SAMPLE_DOC_ID)).resolves.toEqual(doc);
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      `http://localhost:8000/api/v1/documents/${SAMPLE_DOC_ID}/reprocess`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("handles 204 No Content on DELETE without calling response.json()", async () => {
    const fake204Response = {
      ok: true,
      status: 204,
      statusText: "No Content",
      json: vi.fn(() => {
        throw new Error("response.json() must not be called on 204");
      }),
      text: vi.fn(() => {
        throw new Error("response.text() must not be called on 204");
      }),
    } as unknown as Response;

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(fake204Response);

    await expect(deleteDocument(SAMPLE_DOC_ID)).resolves.toBeUndefined();
    expect(fake204Response.json).not.toHaveBeenCalled();
    expect(fake204Response.text).not.toHaveBeenCalled();
  });

  it("handles non-JSON errors, malformed 200 JSON, network failures, and AbortError safely", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response("Bad Gateway", { status: 502, statusText: "Bad Gateway" }),
      )
      .mockResolvedValueOnce(new Response("{not-json", { status: 200 }))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));

    await expect(getHealth()).rejects.toMatchObject({
      code: "HTTP_502",
      status: 502,
      message: "Request failed (502 Bad Gateway).",
    });

    await expect(getHealth()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });

    await expect(getHealth()).rejects.toSatisfy(
      (err: unknown) =>
        err instanceof ApiError &&
        err.isNetworkError &&
        err.code === "NETWORK_ERROR",
    );

    await expect(getHealth()).rejects.toSatisfy((err: unknown) =>
      isAbortError(err),
    );
  });
});
