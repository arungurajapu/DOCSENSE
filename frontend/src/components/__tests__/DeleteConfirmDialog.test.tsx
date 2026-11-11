import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import type { DocumentResponse } from "@/types/api";

function makeDoc(overrides: Partial<DocumentResponse> = {}): DocumentResponse {
  return {
    id: "550e8400-e29b-41d4-a716-446655440099",
    filename: "focus-check.pdf",
    content_type: "application/pdf",
    file_size_bytes: 1024,
    sha256_hash: "c".repeat(64),
    status: "ready",
    error_message: null,
    page_count: 2,
    chunk_count: 4,
    created_at: "2026-11-11T10:00:00Z",
    updated_at: "2026-11-11T10:00:00Z",
    ...overrides,
  };
}

describe("DeleteConfirmDialog", () => {
  it("focuses Cancel when opened and does not steal focus on prop updates or isDeleting transitions", () => {
    const doc = makeDoc();
    const onCancel = vi.fn();
    const onConfirm = vi.fn();

    const { rerender } = render(
      <DeleteConfirmDialog
        document={null}
        isDeleting={false}
        error={null}
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    rerender(
      <DeleteConfirmDialog
        document={doc}
        isDeleting={false}
        error={null}
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    const cancelButton = screen.getByRole("button", { name: /^cancel$/i });
    const confirmButton = screen.getByRole("button", {
      name: /confirm delete/i,
    });
    expect(cancelButton).toHaveFocus();

    // Move focus to Confirm delete and simulate polling parent re-renders
    // (new inline onCancel identity and cloned document object with same id).
    confirmButton.focus();
    expect(confirmButton).toHaveFocus();

    rerender(
      <DeleteConfirmDialog
        document={{ ...doc, updated_at: "2026-11-11T10:01:00Z" }}
        isDeleting={false}
        error={null}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    expect(confirmButton).toHaveFocus();

    // Simulate isDeleting false -> true -> false (e.g. failed delete attempt).
    rerender(
      <DeleteConfirmDialog
        document={doc}
        isDeleting={true}
        error={null}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );
    rerender(
      <DeleteConfirmDialog
        document={doc}
        isDeleting={false}
        error="DOCUMENT_CONFLICT: Cannot delete right now."
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(cancelButton).not.toHaveFocus();
  });

  it("closes on Escape when not deleting and ignores Escape while isDeleting is true", () => {
    const doc = makeDoc();
    const onCancel = vi.fn();

    const { rerender } = render(
      <DeleteConfirmDialog
        document={doc}
        isDeleting={true}
        error={null}
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).not.toHaveBeenCalled();

    const latestOnCancel = vi.fn();
    rerender(
      <DeleteConfirmDialog
        document={doc}
        isDeleting={false}
        error={null}
        onCancel={latestOnCancel}
        onConfirm={vi.fn()}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(latestOnCancel).toHaveBeenCalledTimes(1);
  });
});
