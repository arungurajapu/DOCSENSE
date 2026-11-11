"use client";

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteDocument,
  getHealth,
  isAbortError,
  listDocuments,
  reprocessDocument,
} from "@/lib/api";
import {
  DEFAULT_PAGE_SIZE,
  POLL_INTERVAL_MS,
  getMaxUploadSizeBytes,
  getMaxUploadSizeMb,
} from "@/lib/config";
import {
  isActiveIngestionStatus,
  type DocumentResponse,
  type DocumentStatusFilter,
  type HealthResponse,
} from "@/types/api";
import { DeleteConfirmDialog } from "./DeleteConfirmDialog";
import { DocumentList } from "./DocumentList";
import { HealthBadge } from "./HealthBadge";
import { UploadPanel } from "./UploadPanel";

/**
 * Primary DocSense document ingestion and management dashboard.
 */
export function DocumentsDashboard() {
  const maxSizeMb = getMaxUploadSizeMb();
  const maxSizeBytes = getMaxUploadSizeBytes();

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthLoading, setHealthLoading] = useState<boolean>(true);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthRefreshKey, setHealthRefreshKey] = useState<number>(0);

  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [statusFilter, setStatusFilter] = useState<DocumentStatusFilter>("all");
  const [offset, setOffset] = useState<number>(0);
  const [listRefreshKey, setListRefreshKey] = useState<number>(0);
  const [isLoadingList, setIsLoadingList] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [reprocessingIds, setReprocessingIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [deleteCandidate, setDeleteCandidate] =
    useState<DocumentResponse | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [pollCompletedTick, setPollCompletedTick] = useState<number>(0);

  const isMountedRef = useRef<boolean>(true);
  const isListInFlightRef = useRef<boolean>(false);
  const pollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hasActiveDocuments = documents.some((doc) =>
    isActiveIngestionStatus(doc.status),
  );

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (pollTimeoutRef.current !== null) {
        clearTimeout(pollTimeoutRef.current);
        pollTimeoutRef.current = null;
      }
      isListInFlightRef.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    getHealth(controller.signal)
      .then((response) => {
        if (!active || !isMountedRef.current) {
          return;
        }
        setHealth(response);
        setHealthError(null);
      })
      .catch((caught: unknown) => {
        if (!active || isAbortError(caught) || !isMountedRef.current) {
          return;
        }
        setHealth(null);
        setHealthError(
          caught instanceof Error ? caught.message : "Health check failed.",
        );
      })
      .finally(() => {
        if (active && isMountedRef.current) {
          setHealthLoading(false);
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [healthRefreshKey]);

  useEffect(() => {
    if (pollTimeoutRef.current !== null) {
      clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }

    const controller = new AbortController();
    let active = true;
    isListInFlightRef.current = true;

    listDocuments(
      {
        status: statusFilter,
        limit: DEFAULT_PAGE_SIZE,
        offset,
      },
      controller.signal,
    )
      .then((result) => {
        if (!active || !isMountedRef.current) {
          return;
        }
        setDocuments(result.items);
        setTotal(result.total);
        setListError(null);
      })
      .catch((caught: unknown) => {
        if (!active || isAbortError(caught) || !isMountedRef.current) {
          return;
        }
        setListError(
          caught instanceof Error
            ? caught.message
            : "Failed to load documents.",
        );
      })
      .finally(() => {
        if (active) {
          isListInFlightRef.current = false;
          if (isMountedRef.current) {
            setIsLoadingList(false);
            setPollCompletedTick((tick) => tick + 1);
          }
        }
      });

    return () => {
      active = false;
      isListInFlightRef.current = false;
      controller.abort();
    };
  }, [statusFilter, offset, listRefreshKey]);

  useEffect(() => {
    if (!hasActiveDocuments || isListInFlightRef.current) {
      if (pollTimeoutRef.current !== null) {
        clearTimeout(pollTimeoutRef.current);
        pollTimeoutRef.current = null;
      }
      return;
    }

    pollTimeoutRef.current = setTimeout(() => {
      pollTimeoutRef.current = null;
      if (!isMountedRef.current || isListInFlightRef.current) {
        return;
      }
      setListRefreshKey((key) => key + 1);
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollTimeoutRef.current !== null) {
        clearTimeout(pollTimeoutRef.current);
        pollTimeoutRef.current = null;
      }
    };
  }, [hasActiveDocuments, pollCompletedTick, statusFilter, offset]);

  function handleFilterChange(nextFilter: DocumentStatusFilter) {
    if (nextFilter === statusFilter) {
      return;
    }
    setActionError(null);
    setIsLoadingList(true);
    setStatusFilter(nextFilter);
    setOffset(0);
  }

  function handlePageChange(nextOffset: number) {
    setActionError(null);
    setIsLoadingList(true);
    setOffset(nextOffset);
  }

  function handleUploadSuccess(createdDocument: DocumentResponse) {
    setActionError(null);
    setListError(null);

    const isVisibleInCurrentFilter =
      statusFilter === "all" || statusFilter === createdDocument.status;

    if (!isVisibleInCurrentFilter) {
      setIsLoadingList(true);
      setStatusFilter("all");
      setOffset(0);
      return;
    }

    if (offset !== 0) {
      setIsLoadingList(true);
      setOffset(0);
      return;
    }

    setDocuments((prev) => {
      const exists = prev.some((item) => item.id === createdDocument.id);
      const nextItems = [
        createdDocument,
        ...prev.filter((item) => item.id !== createdDocument.id),
      ].slice(0, DEFAULT_PAGE_SIZE);
      if (!exists) {
        setTotal((prevTotal) => prevTotal + 1);
      }
      return nextItems;
    });
  }

  async function handleReprocess(doc: DocumentResponse) {
    if (reprocessingIds.has(doc.id) || deletingId === doc.id) {
      return;
    }

    setActionError(null);
    setReprocessingIds((prev) => {
      const next = new Set(prev);
      next.add(doc.id);
      return next;
    });

    try {
      const updated = await reprocessDocument(doc.id);
      if (!isMountedRef.current) {
        return;
      }
      setDocuments((prev) =>
        prev.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (caught) {
      if (isAbortError(caught) || !isMountedRef.current) {
        return;
      }
      if (caught instanceof ApiError) {
        setActionError(`${caught.code}: ${caught.message}`);
      } else {
        setActionError(
          caught instanceof Error
            ? caught.message
            : "Failed to reprocess document.",
        );
      }
    } finally {
      if (isMountedRef.current) {
        setReprocessingIds((prev) => {
          const next = new Set(prev);
          next.delete(doc.id);
          return next;
        });
      }
    }
  }

  async function handleConfirmDelete(doc: DocumentResponse) {
    if (deletingId) {
      return;
    }

    setDeleteError(null);
    setActionError(null);
    setDeletingId(doc.id);

    try {
      await deleteDocument(doc.id);
      if (!isMountedRef.current) {
        return;
      }

      const previousCountOnPage = documents.length;
      const previousTotal = total;

      setDeleteCandidate(null);
      setDeletingId(null);

      if (previousCountOnPage === 1 && offset > 0) {
        setIsLoadingList(true);
        setOffset(Math.max(0, offset - DEFAULT_PAGE_SIZE));
        return;
      }

      setDocuments((prev) => prev.filter((item) => item.id !== doc.id));
      setTotal((prevTotal) => Math.max(0, prevTotal - 1));

      if (previousTotal > offset + previousCountOnPage) {
        setListRefreshKey((key) => key + 1);
      }
    } catch (caught) {
      if (isAbortError(caught) || !isMountedRef.current) {
        return;
      }
      const message =
        caught instanceof ApiError
          ? `${caught.code}: ${caught.message}`
          : caught instanceof Error
            ? caught.message
            : "Failed to delete document.";
      setDeleteError(message);
      setDeletingId(null);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900">
              DocSense
            </h1>
            <p className="text-xs text-slate-600">
              Document Ingestion &amp; Lifecycle Management
            </p>
          </div>
          <HealthBadge
            health={health}
            loading={healthLoading}
            error={healthError}
            onRetry={() => {
              setHealthLoading(true);
              setHealthRefreshKey((key) => key + 1);
            }}
          />
        </div>
      </header>

      <main className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8 sm:px-6">
        <UploadPanel
          maxSizeBytes={maxSizeBytes}
          maxSizeMb={maxSizeMb}
          onUploadSuccess={handleUploadSuccess}
        />

        {actionError ? (
          <div
            role="alert"
            className="flex items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
          >
            <span>{actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              aria-label="Dismiss error"
              className="rounded px-2 py-0.5 text-xs font-medium text-rose-700 hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600"
            >
              Dismiss
            </button>
          </div>
        ) : null}

        <DocumentList
          documents={documents}
          total={total}
          limit={DEFAULT_PAGE_SIZE}
          offset={offset}
          statusFilter={statusFilter}
          loading={isLoadingList}
          isPolling={hasActiveDocuments}
          error={listError}
          reprocessingIds={reprocessingIds}
          deletingId={deletingId}
          onFilterChange={handleFilterChange}
          onPageChange={handlePageChange}
          onRetry={() => {
            setIsLoadingList(true);
            setListRefreshKey((key) => key + 1);
          }}
          onReprocess={(doc) => {
            void handleReprocess(doc);
          }}
          onRequestDelete={(doc) => {
            setDeleteError(null);
            setDeleteCandidate(doc);
          }}
        />
      </main>

      <DeleteConfirmDialog
        document={deleteCandidate}
        isDeleting={deletingId !== null}
        error={deleteError}
        onCancel={() => {
          if (deletingId === null) {
            setDeleteCandidate(null);
            setDeleteError(null);
          }
        }}
        onConfirm={(doc) => {
          void handleConfirmDelete(doc);
        }}
      />
    </div>
  );
}
