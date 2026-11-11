"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, isAbortError, uploadDocument } from "@/lib/api";
import { validatePdfFile } from "@/lib/validation";
import type { DocumentResponse } from "@/types/api";

export interface UploadPanelProps {
  maxSizeBytes: number;
  maxSizeMb: number;
  onUploadSuccess: (document: DocumentResponse) => void;
}

interface UploadErrorState {
  code?: string;
  message: string;
}

/**
 * PDF upload panel supporting drag-and-drop and keyboard-accessible file selection,
 * client-side PDF/non-empty/max-size validation, upload progress state, and
 * structured ErrorResponse display.
 */
export function UploadPanel({
  maxSizeBytes,
  maxSizeMb,
  onUploadSuccess,
}: UploadPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepthRef = useRef(0);
  const abortControllerRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef(true);
  const onUploadSuccessRef = useRef(onUploadSuccess);

  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(
    null,
  );
  const [error, setError] = useState<UploadErrorState | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    onUploadSuccessRef.current = onUploadSuccess;
  }, [onUploadSuccess]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
    };
  }, []);

  async function processSelectedFile(file: File) {
    if (isUploading) {
      return;
    }

    setError(null);
    setSuccessMessage(null);

    const validation = validatePdfFile(file, maxSizeBytes);
    if (!validation.valid) {
      setError({
        code: "CLIENT_VALIDATION_ERROR",
        message: validation.error ?? "Invalid PDF file.",
      });
      return;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    setIsUploading(true);
    setUploadingFilename(file.name);

    try {
      const createdDocument = await uploadDocument(file, controller.signal);
      if (!isMountedRef.current) {
        return;
      }
      setSuccessMessage(
        `"${createdDocument.filename}" queued for ingestion.`,
      );
      onUploadSuccessRef.current(createdDocument);
    } catch (caught) {
      if (isAbortError(caught) || !isMountedRef.current) {
        return;
      }
      if (caught instanceof ApiError) {
        setError({
          code: caught.code,
          message: caught.message,
        });
      } else {
        setError({
          message:
            caught instanceof Error
              ? caught.message
              : "Failed to upload PDF document.",
        });
      }
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      if (isMountedRef.current) {
        setIsUploading(false);
        setUploadingFilename(null);
      }
    }
  }

  function handleInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0];
    event.target.value = "";
    if (!selectedFile) {
      return;
    }
    void processSelectedFile(selectedFile);
  }

  function handleDragEnter(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (isUploading) {
      return;
    }
    dragDepthRef.current += 1;
    setIsDragging(true);
  }

  function handleDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (!isUploading && event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
  }

  function handleDragLeave(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDragging(false);
    }
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setIsDragging(false);

    if (isUploading) {
      return;
    }

    const files = event.dataTransfer?.files;
    if (!files || files.length === 0) {
      return;
    }
    if (files.length > 1) {
      setSuccessMessage(null);
      setError({
        code: "MULTIPLE_FILES_NOT_SUPPORTED",
        message: "Please upload one PDF file at a time.",
      });
      return;
    }

    const droppedFile = files[0];
    if (droppedFile) {
      void processSelectedFile(droppedFile);
    }
  }

  return (
    <section
      aria-labelledby="upload-section-heading"
      className="rounded-xl border border-slate-200 bg-white p-6 shadow-xs"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2
            id="upload-section-heading"
            className="text-base font-semibold text-slate-900"
          >
            Upload PDF Document
          </h2>
          <p className="mt-0.5 text-sm text-slate-600">
            Select or drag a PDF to queue text extraction, chunking, and vector
            indexing.
          </p>
        </div>
        <span className="text-xs font-medium text-slate-500">
          PDF only · Max {maxSizeMb} MB
        </span>
      </div>

      <div
        data-testid="pdf-dropzone"
        aria-busy={isUploading}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`mt-4 flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          isUploading
            ? "border-slate-300 bg-slate-50 opacity-80"
            : isDragging
              ? "border-sky-500 bg-sky-50"
              : "border-slate-300 bg-slate-50/70 hover:border-slate-400"
        }`}
      >
        <input
          ref={fileInputRef}
          id="pdf-upload-input"
          type="file"
          accept=".pdf,application/pdf"
          disabled={isUploading}
          aria-label="Choose PDF file"
          onChange={handleInputChange}
          className="sr-only"
        />

        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          className="h-9 w-9 text-slate-400"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 16.5V9.75m0 0-3 3m3-3 3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z"
          />
        </svg>

        {isUploading ? (
          <div className="mt-3 flex flex-col items-center gap-1" role="status">
            <p className="text-sm font-medium text-slate-900">
              Uploading {uploadingFilename ?? "PDF"}…
            </p>
            <p className="text-xs text-slate-500">
              Sending file to ingestion queue
            </p>
          </div>
        ) : (
          <>
            <p className="mt-3 text-sm font-medium text-slate-800">
              Drag and drop a PDF file here, or
            </p>
            <button
              type="button"
              disabled={isUploading}
              onClick={() => fileInputRef.current?.click()}
              className="mt-3 inline-flex items-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-slate-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Choose PDF file
            </button>
            <p className="mt-2 text-xs text-slate-500">
              Accepted format: <code className="font-mono">.pdf</code> (non-empty,
              up to {maxSizeMb} MB)
            </p>
          </>
        )}
      </div>

      {error ? (
        <div
          role="alert"
          className="mt-4 flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
        >
          <div className="space-y-0.5">
            {error.code ? (
              <p className="font-mono text-xs font-semibold uppercase tracking-wide text-rose-700">
                {error.code}
              </p>
            ) : null}
            <p>{error.message}</p>
          </div>
          <button
            type="button"
            onClick={() => setError(null)}
            aria-label="Dismiss upload error"
            className="shrink-0 rounded px-2 py-0.5 text-xs font-medium text-rose-700 hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {successMessage ? (
        <div
          role="status"
          className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900"
        >
          <p>{successMessage}</p>
          <button
            type="button"
            onClick={() => setSuccessMessage(null)}
            aria-label="Dismiss upload confirmation"
            className="shrink-0 rounded px-2 py-0.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
          >
            Dismiss
          </button>
        </div>
      ) : null}
    </section>
  );
}
