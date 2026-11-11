import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UploadPanel } from "@/components/UploadPanel";
import type { DocumentResponse } from "@/types/api";

const ACCEPTED_DOC: DocumentResponse = {
  id: "550e8400-e29b-41d4-a716-446655440001",
  filename: "architecture.pdf",
  content_type: "application/pdf",
  file_size_bytes: 4096,
  sha256_hash: "b".repeat(64),
  status: "pending",
  error_message: null,
  page_count: null,
  chunk_count: 0,
  created_at: "2026-11-11T10:00:00Z",
  updated_at: "2026-11-11T10:00:00Z",
};

describe("UploadPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rejects non-PDF, empty, and oversized files client-side without calling fetch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const onUploadSuccess = vi.fn();

    render(
      <UploadPanel
        maxSizeBytes={1024}
        maxSizeMb={1}
        onUploadSuccess={onUploadSuccess}
      />,
    );

    const input = screen.getByLabelText(/choose pdf file/i);

    const txtFile = new File(["hello"], "readme.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [txtFile] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /only pdf files \(\.pdf\) are supported/i,
    );

    const emptyPdf = new File([], "empty.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [emptyPdf] } });
    expect(screen.getByRole("alert")).toHaveTextContent(/empty \(0 bytes\)/i);

    const largePdf = new File([new Uint8Array(2048)], "large.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [largePdf] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /exceeds the maximum upload limit/i,
    );

    expect(fetchMock).not.toHaveBeenCalled();
    expect(onUploadSuccess).not.toHaveBeenCalled();
  });

  it("shows loading state during upload and calls onUploadSuccess on 202 via drag-and-drop", async () => {
    let resolveFetch: (value: Response) => void = () => {};
    const pendingPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockReturnValueOnce(pendingPromise);
    const onUploadSuccess = vi.fn();

    render(
      <UploadPanel
        maxSizeBytes={25 * 1024 * 1024}
        maxSizeMb={25}
        onUploadSuccess={onUploadSuccess}
      />,
    );

    const dropzone = screen.getByTestId("pdf-dropzone");
    const pdfFile = new File(["%PDF-1.4"], "architecture.PDF", {
      type: "application/pdf",
    });

    fireEvent.drop(dropzone, {
      dataTransfer: { files: [pdfFile] },
    });

    expect(
      screen.getByText(/uploading architecture\.PDF/i),
    ).toBeInTheDocument();
    expect(dropzone).toHaveAttribute("aria-busy", "true");

    resolveFetch(
      new Response(JSON.stringify(ACCEPTED_DOC), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await waitFor(() => {
      expect(onUploadSuccess).toHaveBeenCalledWith(ACCEPTED_DOC);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent(
      /architecture\.pdf.*queued for ingestion/i,
    );
  });

  it("renders unified ErrorResponse messages for 413, 415, and 422 responses", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: {
              code: "FILE_TOO_LARGE",
              message: "File exceeds 25 MB server limit.",
            },
          }),
          { status: 413 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: {
              code: "UNSUPPORTED_MEDIA_TYPE",
              message: "Uploaded file is not a valid PDF header.",
            },
          }),
          { status: 415 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: {
              code: "ENCRYPTED_PDF_NOT_SUPPORTED",
              message: "Encrypted PDFs are not supported.",
            },
          }),
          { status: 422 },
        ),
      );

    render(
      <UploadPanel
        maxSizeBytes={25 * 1024 * 1024}
        maxSizeMb={25}
        onUploadSuccess={vi.fn()}
      />,
    );

    const input = screen.getByLabelText(/choose pdf file/i);
    const pdfFile = new File(["%PDF-1.4"], "test.pdf", {
      type: "application/pdf",
    });

    await user.upload(input, pdfFile);
    expect(await screen.findByRole("alert")).toHaveTextContent("FILE_TOO_LARGE");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "File exceeds 25 MB server limit.",
    );

    await user.upload(input, pdfFile);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "UNSUPPORTED_MEDIA_TYPE",
    );

    await user.upload(input, pdfFile);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "ENCRYPTED_PDF_NOT_SUPPORTED",
    );
  });
});
