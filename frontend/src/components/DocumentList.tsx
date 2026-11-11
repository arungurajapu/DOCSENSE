"use client";

import {
  formatChunkCount,
  formatFileSize,
  formatPageCount,
  formatUtcTimestamp,
} from "@/lib/format";
import {
  DOCUMENT_FILTER_TABS,
  type DocumentResponse,
  type DocumentStatusFilter,
} from "@/types/api";
import { StatusBadge } from "./StatusBadge";

export interface DocumentListProps {
  documents: DocumentResponse[];
  total: number;
  limit: number;
  offset: number;
  statusFilter: DocumentStatusFilter;
  loading: boolean;
  isPolling: boolean;
  error: string | null;
  reprocessingIds: ReadonlySet<string>;
  deletingId: string | null;
  onFilterChange: (filter: DocumentStatusFilter) => void;
  onPageChange: (nextOffset: number) => void;
  onRetry: () => void;
  onReprocess: (document: DocumentResponse) => void;
  onRequestDelete: (document: DocumentResponse) => void;
}

const FILTER_LABELS: Record<DocumentStatusFilter, string> = {
  all: "All",
  pending: "Pending",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
  deleting: "Deleting",
};

/**
 * Displays filter tabs, document metadata cards, lifecycle status badges,
 * failure diagnostics, reprocess/delete controls, and pagination.
 */
export function DocumentList({
  documents,
  total,
  limit,
  offset,
  statusFilter,
  loading,
  isPolling,
  error,
  reprocessingIds,
  deletingId,
  onFilterChange,
  onPageChange,
  onRetry,
  onReprocess,
  onRequestDelete,
}: DocumentListProps) {
  const startItem = total === 0 ? 0 : offset + 1;
  const endItem = Math.min(offset + documents.length, total);
  const hasPrevious = offset > 0;
  const hasNext = offset + documents.length < total;

  return (
    <section
      aria-labelledby="documents-section-heading"
      className="rounded-xl border border-slate-200 bg-white p-6 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2
            id="documents-section-heading"
            className="text-base font-semibold text-slate-900"
          >
            Documents
          </h2>
          <p className="mt-0.5 text-sm text-slate-600">
            Track PDF ingestion status, chunk counts, and cryptographic hashes.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {isPolling ? (
            <span
              role="status"
              aria-live="polite"
              className="inline-flex items-center gap-1.5 rounded-full border border-sky-200 bg-sky-50 px-3 py-1 text-xs font-medium text-sky-800"
            >
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse"
              />
              Auto-refreshing (2s)
            </span>
          ) : null}
          <span className="text-xs font-medium text-slate-500">
            {total} {total === 1 ? "document" : "documents"}
          </span>
        </div>
      </div>

      <div
        role="tablist"
        aria-label="Filter documents by status"
        className="mt-4 flex flex-wrap gap-1.5 border-b border-slate-200 pb-3"
      >
        {DOCUMENT_FILTER_TABS.map((tab) => {
          const selected = statusFilter === tab;
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => onFilterChange(tab)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 ${
                selected
                  ? "bg-slate-900 text-white"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
            >
              {FILTER_LABELS[tab]}
            </button>
          );
        })}
      </div>

      {error ? (
        <div
          role="alert"
          className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-rose-300 bg-white px-3 py-1 text-xs font-medium text-rose-800 hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600"
          >
            Retry
          </button>
        </div>
      ) : null}

      {loading && documents.length === 0 ? (
        <div
          role="status"
          className="mt-6 flex items-center justify-center rounded-lg border border-slate-200 bg-slate-50 py-12 text-sm text-slate-600"
        >
          Loading documents…
        </div>
      ) : !error && documents.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-slate-200 bg-slate-50/70 px-6 py-12 text-center">
          <p className="text-sm font-medium text-slate-800">
            {statusFilter === "all"
              ? "No documents uploaded yet"
              : `No ${FILTER_LABELS[statusFilter].toLowerCase()} documents`}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {statusFilter === "all"
              ? "Upload a PDF above to start ingestion."
              : "Switch tabs or upload another PDF to view documents in other states."}
          </p>
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-slate-200">
          {documents.map((doc) => {
            const isDeletingRow =
              doc.status === "deleting" || deletingId === doc.id;
            const isReprocessingRow = reprocessingIds.has(doc.id);
            const isRowBusy = isDeletingRow || isReprocessingRow;

            return (
              <li key={doc.id} className="py-4 first:pt-2 last:pb-2">
                <article
                  aria-label={doc.filename}
                  className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-slate-50/40 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <h3 className="truncate text-sm font-semibold text-slate-900">
                          {doc.filename}
                        </h3>
                        <StatusBadge status={doc.status} />
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        {doc.content_type} · ID{" "}
                        <code className="font-mono">{doc.id}</code>
                      </p>
                    </div>

                    <div className="flex shrink-0 items-center gap-2">
                      {doc.status === "failed" ? (
                        <button
                          type="button"
                          disabled={isRowBusy}
                          aria-label={`Reprocess ${doc.filename}`}
                          onClick={() => onReprocess(doc)}
                          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-800 shadow-2xs hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isReprocessingRow ? "Reprocessing…" : "Reprocess"}
                        </button>
                      ) : null}

                      <button
                        type="button"
                        disabled={isRowBusy}
                        aria-label={`Delete ${doc.filename}`}
                        onClick={() => onRequestDelete(doc)}
                        className="rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-medium text-rose-700 shadow-2xs hover:bg-rose-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {isDeletingRow ? "Deleting…" : "Delete"}
                      </button>
                    </div>
                  </div>

                  {doc.error_message ? (
                    <div
                      role="note"
                      className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900"
                    >
                      <span className="font-semibold">Ingestion error: </span>
                      <span>{doc.error_message}</span>
                    </div>
                  ) : null}

                  <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
                    <div>
                      <dt className="text-slate-500">Size</dt>
                      <dd className="mt-0.5 font-medium text-slate-900">
                        {formatFileSize(doc.file_size_bytes)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Pages</dt>
                      <dd className="mt-0.5 font-medium text-slate-900">
                        {formatPageCount(doc.page_count)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Chunks</dt>
                      <dd className="mt-0.5 font-medium text-slate-900">
                        {formatChunkCount(doc.chunk_count)}
                      </dd>
                    </div>
                    <div className="col-span-2 lg:col-span-1">
                      <dt className="text-slate-500">SHA-256</dt>
                      <dd className="mt-0.5">
                        <code
                          title={doc.sha256_hash}
                          className="font-mono text-[11px] text-slate-800 break-all"
                        >
                          {doc.sha256_hash}
                        </code>
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Created</dt>
                      <dd className="mt-0.5 font-mono text-[11px] text-slate-700">
                        {formatUtcTimestamp(doc.created_at)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">Updated</dt>
                      <dd className="mt-0.5 font-mono text-[11px] text-slate-700">
                        {formatUtcTimestamp(doc.updated_at)}
                      </dd>
                    </div>
                  </dl>
                </article>
              </li>
            );
          })}
        </ul>
      )}

      {total > 0 && (hasPrevious || hasNext) ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4 text-xs text-slate-600">
          <span>
            Showing {startItem}–{endItem} of {total}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!hasPrevious || loading}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!hasNext || loading}
              onClick={() => onPageChange(offset + limit)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
