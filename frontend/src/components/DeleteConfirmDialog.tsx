"use client";

import { useEffect, useRef } from "react";
import type { DocumentResponse } from "@/types/api";

export interface DeleteConfirmDialogProps {
  document: DocumentResponse | null;
  isDeleting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: (document: DocumentResponse) => void;
}

/**
 * Keyboard-accessible confirmation modal before deleting a document
 * and its indexed chunks.
 */
export function DeleteConfirmDialog({
  document,
  isDeleting,
  error,
  onCancel,
  onConfirm,
}: DeleteConfirmDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const onCancelRef = useRef(onCancel);

  const documentId = document?.id ?? null;
  const isOpen = documentId !== null;

  useEffect(() => {
    onCancelRef.current = onCancel;
  }, [onCancel]);

  useEffect(() => {
    if (documentId === null) {
      return;
    }
    cancelButtonRef.current?.focus();
  }, [documentId]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isDeleting) {
        onCancelRef.current();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, isDeleting]);

  if (!document) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
      onClick={(event) => {
        if (event.target === event.currentTarget && !isDeleting) {
          onCancel();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-dialog-title"
        aria-describedby="delete-dialog-description"
        className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-xl"
      >
        <h2
          id="delete-dialog-title"
          className="text-base font-semibold text-slate-900"
        >
          Delete document?
        </h2>
        <p
          id="delete-dialog-description"
          className="mt-2 text-sm text-slate-600"
        >
          This will permanently remove{" "}
          <span className="font-medium text-slate-900 break-all">
            {document.filename}
          </span>{" "}
          and all of its indexed chunks from storage. This action cannot be
          undone.
        </p>

        {error ? (
          <div
            role="alert"
            className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800"
          >
            {error}
          </div>
        ) : null}

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            ref={cancelButtonRef}
            type="button"
            disabled={isDeleting}
            onClick={onCancel}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={isDeleting}
            onClick={() => {
              if (!isDeleting) {
                onConfirm(document);
              }
            }}
            className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isDeleting ? "Deleting…" : "Confirm delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
