import { StrictMode } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentsDashboard } from "@/components/DocumentsDashboard";
import type {
  DocumentListResponse,
  DocumentResponse,
  HealthResponse,
} from "@/types/api";

function makeHealth(overrides: Partial<HealthResponse> = {}): HealthResponse {
  return {
    status: "ok",
    version: "0.1.0",
    database: "ok",
    vector_store: "ok",
    embedding_model_loaded: true,
    timestamp: "2026-11-11T10:00:00Z",
    ...overrides,
  };
}

function makeDoc(overrides: Partial<DocumentResponse> = {}): DocumentResponse {
  return {
    id: "550e8400-e29b-41d4-a716-446655440010",
    filename: "report.pdf",
    content_type: "application/pdf",
    file_size_bytes: 1536,
    sha256_hash:
      "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    status: "ready",
    error_message: null,
    page_count: 5,
    chunk_count: 14,
    created_at: "2026-11-11T10:00:00Z",
    updated_at: "2026-11-11T10:02:00Z",
    ...overrides,
  };
}

function makeListResponse(
  items: DocumentResponse[],
  total = items.length,
): DocumentListResponse {
  return {
    items,
    total,
    limit: 20,
    offset: 0,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("DocumentsDashboard", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("displays health states (ok, degraded when embedding_model_loaded=false, and unreachable with retry)", async () => {
    let healthPhase = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        healthPhase += 1;
        if (healthPhase === 1) {
          return jsonResponse(
            makeHealth({ status: "ok", embedding_model_loaded: false }),
          );
        }
        if (healthPhase === 2) {
          return jsonResponse(
            {
              error: {
                code: "SERVICE_UNAVAILABLE",
                message: "Backend booting",
              },
            },
            503,
          );
        }
        return jsonResponse(makeHealth());
      }
      return jsonResponse(makeListResponse([]));
    });

    render(<DocumentsDashboard />);

    expect(await screen.findByText("API Degraded")).toBeInTheDocument();
    expect(screen.getByText(/embeddings: not loaded/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(await screen.findByText("API Unreachable")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
    expect(await screen.findByText("API OK")).toBeInTheDocument();
  });

  it("renders metadata including 0 vs null page_count, deleting state, and status filter queries", async () => {
    const docZeroPages = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440011",
      filename: "zero-pages.pdf",
      status: "ready",
      page_count: 0,
      chunk_count: 0,
    });
    const docNullPages = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440012",
      filename: "removing.pdf",
      status: "deleting",
      page_count: null,
      chunk_count: 3,
    });

    const listUrls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      listUrls.push(url);
      if (url.includes("status=ready")) {
        return jsonResponse(makeListResponse([docZeroPages]));
      }
      if (url.includes("status=failed")) {
        return jsonResponse(makeListResponse([]));
      }
      return jsonResponse(makeListResponse([docZeroPages, docNullPages]));
    });

    render(<DocumentsDashboard />);

    const zeroCard = await screen.findByRole("article", {
      name: "zero-pages.pdf",
    });
    const zeroPagesValue =
      within(zeroCard).getByText("Pages").nextElementSibling;
    const zeroChunksValue =
      within(zeroCard).getByText("Chunks").nextElementSibling;
    expect(zeroPagesValue).toHaveTextContent("0");
    expect(zeroChunksValue).toHaveTextContent("0");
    expect(within(zeroCard).getByText(docZeroPages.sha256_hash)).toBeInTheDocument();
    expect(within(zeroCard).getByText("2026-11-11 10:00:00 UTC")).toBeInTheDocument();
    expect(within(zeroCard).getByText("2026-11-11 10:02:00 UTC")).toBeInTheDocument();

    const deletingCard = screen.getByRole("article", { name: "removing.pdf" });
    const nullPagesValue =
      within(deletingCard).getByText("Pages").nextElementSibling;
    expect(nullPagesValue).toHaveTextContent("—");
    expect(
      within(deletingCard).getByRole("button", { name: /delete removing\.pdf/i }),
    ).toBeDisabled();

    expect(listUrls[0]).toBe(
      "http://localhost:8000/api/v1/documents?limit=20&offset=0",
    );

    fireEvent.click(screen.getByRole("tab", { name: "Failed" }));
    expect(await screen.findByText(/no failed documents/i)).toBeInTheDocument();
    expect(listUrls).toContain(
      "http://localhost:8000/api/v1/documents?status=failed&limit=20&offset=0",
    );

    // Scope guard: no RAG Q&A / chat / auth controls exist
    expect(screen.queryByPlaceholderText(/ask a question/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign in/i })).not.toBeInTheDocument();
  });

  it("uploads a PDF (202), polls every 2s across pending -> processing -> ready, and stops polling once terminal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });

    const docPending = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440020",
      filename: "manual.pdf",
      status: "pending",
      page_count: null,
      chunk_count: 0,
    });
    const docProcessing = makeDoc({
      ...docPending,
      status: "processing",
      page_count: 6,
    });
    const docReady = makeDoc({
      ...docPending,
      status: "ready",
      page_count: 6,
      chunk_count: 18,
    });

    let getDocumentsCalls = 0;

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      if (url.includes("/api/v1/documents") && init?.method === "POST") {
        return jsonResponse(docPending, 202);
      }
      if (url.includes("/api/v1/documents")) {
        getDocumentsCalls += 1;
        if (getDocumentsCalls === 1) {
          return jsonResponse(makeListResponse([]));
        }
        if (getDocumentsCalls === 2) {
          return jsonResponse(makeListResponse([docProcessing]));
        }
        return jsonResponse(makeListResponse([docReady]));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<DocumentsDashboard />);

    await act(async () => {
      await Promise.resolve();
    });
    expect(getDocumentsCalls).toBe(1);
    expect(screen.getByText(/no documents uploaded yet/i)).toBeInTheDocument();

    // Idle list should NOT poll
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(getDocumentsCalls).toBe(1);

    // Upload valid PDF -> 202 Pending
    const input = screen.getByTestId("pdf-upload-input");
    const pdfFile = new File(["%PDF-1.4"], "manual.pdf", {
      type: "application/pdf",
    });

    await act(async () => {
      fireEvent.change(input, { target: { files: [pdfFile] } });
      await Promise.resolve();
    });

    expect(
      screen.getByRole("article", { name: "manual.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/auto-refreshing \(2s\)/i)).toBeInTheDocument();

    // First 2s poll -> processing
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
      await Promise.resolve();
    });
    expect(getDocumentsCalls).toBe(2);
    expect(
      within(screen.getByRole("article", { name: "manual.pdf" })).getByText(
        "Processing",
      ),
    ).toBeInTheDocument();

    // Second 2s poll -> ready (terminal)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
      await Promise.resolve();
    });
    expect(getDocumentsCalls).toBe(3);
    const readyCard = screen.getByRole("article", { name: "manual.pdf" });
    expect(within(readyCard).getByText("Ready")).toBeInTheDocument();
    expect(within(readyCard).getByText("18")).toBeInTheDocument();
    expect(
      screen.queryByText(/auto-refreshing \(2s\)/i),
    ).not.toBeInTheDocument();

    // Verify polling stops completely after terminal state
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(getDocumentsCalls).toBe(3);
  });

  it("shows failure error_message, triggers reprocess (202), restarts polling, and cleans up timers/abort on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });

    const failedDoc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440030",
      filename: "broken.pdf",
      status: "failed",
      error_message: "PDF contains no extractable text.",
      page_count: 1,
      chunk_count: 0,
    });
    const reprocessedPending = makeDoc({
      ...failedDoc,
      status: "pending",
      error_message: null,
      page_count: null,
    });

    let getCalls = 0;
    const abortedSignals: AbortSignal[] = [];

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      if (url.endsWith(`/api/v1/documents/${failedDoc.id}/reprocess`)) {
        return jsonResponse(reprocessedPending, 202);
      }
      if (url.includes("/api/v1/documents")) {
        getCalls += 1;
        if (getCalls === 1) {
          return jsonResponse(makeListResponse([failedDoc]));
        }
        // Keep second poll pending until unmount to verify AbortController cleanup
        return new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal;
          if (signal) {
            abortedSignals.push(signal);
            signal.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          }
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    const { unmount } = render(<DocumentsDashboard />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(
      screen.getByText("PDF contains no extractable text."),
    ).toBeInTheDocument();

    const reprocessButton = screen.getByRole("button", {
      name: /reprocess broken\.pdf/i,
    });

    await act(async () => {
      fireEvent.click(reprocessButton);
      await Promise.resolve();
    });

    expect(
      within(screen.getByRole("article", { name: "broken.pdf" })).getByText(
        "Pending",
      ),
    ).toBeInTheDocument();

    // Advance 2s to start poll request, then advance another 4s while still in-flight:
    // overlapping poll requests must NOT happen.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
      await Promise.resolve();
    });
    expect(getCalls).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(getCalls).toBe(2);

    unmount();
    expect(abortedSignals[0]?.aborted).toBe(true);
  });

  it("requires delete confirmation, handles 204 No Content, and prevents double-submission", async () => {
    const doc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440040",
      filename: "removable.pdf",
      status: "ready",
    });

    let resolveDelete: (res: Response) => void = () => {};
    let deleteCallCount = 0;

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      if (
        url.endsWith(`/api/v1/documents/${doc.id}`) &&
        init?.method === "DELETE"
      ) {
        deleteCallCount += 1;
        return new Promise<Response>((resolve) => {
          resolveDelete = resolve;
        });
      }
      if (url.includes("/api/v1/documents")) {
        return jsonResponse(makeListResponse([doc]));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<DocumentsDashboard />);

    const deleteButton = await screen.findByRole("button", {
      name: /delete removable\.pdf/i,
    });
    fireEvent.click(deleteButton);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/delete document\?/i);

    // Pressing Escape cancels without deleting
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteCallCount).toBe(0);

    // Re-open and confirm; rapid double click submits only once
    fireEvent.click(deleteButton);
    const confirmButton = within(screen.getByRole("dialog")).getByRole(
      "button",
      { name: /confirm delete/i },
    );
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);

    expect(deleteCallCount).toBe(1);
    expect(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: /deleting/i,
      }),
    ).toBeDisabled();

    resolveDelete(new Response(null, { status: 204 }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(
      screen.queryByRole("article", { name: "removable.pdf" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/no documents uploaded yet/i)).toBeInTheDocument();
  });

  it("handles list fetch errors with retry, conflict errors on reprocess/delete, and pagination", async () => {
    const failedDoc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440050",
      filename: "page1.pdf",
      status: "failed",
      error_message: "Parse error",
    });
    const page2Doc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440051",
      filename: "page2.pdf",
      status: "ready",
    });

    let listCalls = 0;

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      if (url.endsWith(`/api/v1/documents/${failedDoc.id}/reprocess`)) {
        return jsonResponse(
          {
            error: {
              code: "DOCUMENT_CONFLICT",
              message: "Document is already queued.",
            },
          },
          409,
        );
      }
      if (
        url.endsWith(`/api/v1/documents/${failedDoc.id}`) &&
        init?.method === "DELETE"
      ) {
        return jsonResponse(
          {
            error: {
              code: "DOCUMENT_CONFLICT",
              message: "Document deletion already in progress.",
            },
          },
          409,
        );
      }
      if (url.includes("/api/v1/documents")) {
        listCalls += 1;
        if (listCalls === 1) {
          return jsonResponse(
            {
              error: {
                code: "INTERNAL_ERROR",
                message: "Temporary database failure.",
              },
            },
            500,
          );
        }
        if (url.includes("offset=20")) {
          return jsonResponse({
            items: [page2Doc],
            total: 21,
            limit: 20,
            offset: 20,
          });
        }
        return jsonResponse({
          items: [failedDoc],
          total: 21,
          limit: 20,
          offset: 0,
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<DocumentsDashboard />);

    const listAlert = await screen.findByRole("alert");
    expect(listAlert).toHaveTextContent("Temporary database failure.");

    fireEvent.click(within(listAlert).getByRole("button", { name: /retry/i }));
    expect(
      await screen.findByRole("article", { name: "page1.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Showing 1–1 of 21")).toBeInTheDocument();

    // Reprocess 409 error appears in alert banner
    fireEvent.click(
      screen.getByRole("button", { name: /reprocess page1\.pdf/i }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /DOCUMENT_CONFLICT: Document is already queued\./,
    );

    // Delete 409 error appears inside confirmation dialog
    fireEvent.click(
      screen.getByRole("button", { name: /delete page1\.pdf/i }),
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: /confirm delete/i }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /DOCUMENT_CONFLICT: Document deletion already in progress\./,
    );
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    // Next page pagination
    fireEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(
      await screen.findByRole("article", { name: "page2.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Showing 21–21 of 21")).toBeInTheDocument();
  });

  it("keeps focus on Confirm delete across 2s polling updates while delete dialog is open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });

    const activeDoc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440060",
      filename: "active.pdf",
      status: "pending",
      page_count: null,
      chunk_count: 0,
    });
    const targetDoc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440061",
      filename: "target.pdf",
      status: "ready",
    });

    let getCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      if (url.includes("/api/v1/documents")) {
        getCalls += 1;
        const polledActive =
          getCalls === 1
            ? activeDoc
            : { ...activeDoc, status: "processing" as const };
        return jsonResponse(makeListResponse([polledActive, targetDoc]));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<DocumentsDashboard />);

    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(
      screen.getByRole("button", { name: /delete target\.pdf/i }),
    );
    const dialog = screen.getByRole("dialog");
    const cancelButton = within(dialog).getByRole("button", {
      name: /^cancel$/i,
    });
    const confirmButton = within(dialog).getByRole("button", {
      name: /confirm delete/i,
    });

    expect(cancelButton).toHaveFocus();
    confirmButton.focus();
    expect(confirmButton).toHaveFocus();

    // Advance across multiple 2s poll cycles; polling re-renders must not steal focus.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
      await Promise.resolve();
    });
    expect(getCalls).toBe(2);
    expect(confirmButton).toHaveFocus();
    expect(cancelButton).not.toHaveFocus();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
      await Promise.resolve();
    });
    expect(getCalls).toBe(3);
    expect(confirmButton).toHaveFocus();
  });

  it("increments total by exactly 1 on upload when rendered under React StrictMode", async () => {
    const existingDoc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440070",
      filename: "existing.pdf",
      status: "ready",
    });
    const uploadedDoc = makeDoc({
      id: "550e8400-e29b-41d4-a716-446655440071",
      filename: "new-strict.pdf",
      status: "pending",
      page_count: null,
      chunk_count: 0,
    });

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) {
        return jsonResponse(makeHealth());
      }
      if (url.includes("/api/v1/documents") && init?.method === "POST") {
        return jsonResponse(uploadedDoc, 202);
      }
      if (url.includes("/api/v1/documents")) {
        return jsonResponse(makeListResponse([existingDoc], 1));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(
      <StrictMode>
        <DocumentsDashboard />
      </StrictMode>,
    );

    expect(
      await screen.findByRole("article", { name: "existing.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByText("1 document")).toBeInTheDocument();

    const input = screen.getByTestId("pdf-upload-input");
    const pdfFile = new File(["%PDF-1.4"], "new-strict.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [pdfFile] } });

    expect(
      await screen.findByRole("article", { name: "new-strict.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 documents")).toBeInTheDocument();
    expect(screen.queryByText("3 documents")).not.toBeInTheDocument();
  });
});
