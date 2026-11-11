import type { HealthResponse } from "@/types/api";

export interface HealthBadgeProps {
  health: HealthResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

/**
 * Displays backend API readiness, including degraded state when dependencies
 * or the embedding model are not ready, or when the health endpoint is unreachable.
 */
export function HealthBadge({
  health,
  loading,
  error,
  onRetry,
}: HealthBadgeProps) {
  if (loading && !health && !error) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 shadow-xs"
      >
        <span
          aria-hidden="true"
          className="h-2 w-2 rounded-full bg-slate-400 animate-pulse"
        />
        <span>Checking API health…</span>
      </div>
    );
  }

  if (error || !health) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex flex-wrap items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-800"
      >
        <span
          aria-hidden="true"
          className="h-2 w-2 rounded-full bg-rose-500"
        />
        <span className="font-semibold">API Unreachable</span>
        {error ? <span className="text-rose-700">— {error}</span> : null}
        <button
          type="button"
          onClick={onRetry}
          disabled={loading}
          className="ml-1 rounded border border-rose-300 bg-white px-2 py-0.5 font-medium text-rose-800 hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 disabled:opacity-50"
        >
          {loading ? "Checking…" : "Retry"}
        </button>
      </div>
    );
  }

  const isDegraded =
    health.status === "degraded" ||
    !health.embedding_model_loaded ||
    health.database !== "ok" ||
    health.vector_store !== "ok";

  const detailSummary = [
    `DB: ${health.database}`,
    `Vector store: ${health.vector_store}`,
    `Embeddings: ${health.embedding_model_loaded ? "loaded" : "not loaded"}`,
  ].join(" · ");

  if (isDegraded) {
    return (
      <div
        role="status"
        aria-live="polite"
        title={detailSummary}
        className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-900"
      >
        <span
          aria-hidden="true"
          className="h-2 w-2 rounded-full bg-amber-500"
        />
        <span className="font-semibold">API Degraded</span>
        <span className="text-amber-800">({detailSummary})</span>
        <button
          type="button"
          onClick={onRetry}
          disabled={loading}
          className="ml-1 rounded border border-amber-300 bg-white px-2 py-0.5 font-medium text-amber-900 hover:bg-amber-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600 disabled:opacity-50"
        >
          {loading ? "Checking…" : "Refresh"}
        </button>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      title={detailSummary}
      className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs text-emerald-900"
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 rounded-full bg-emerald-500"
      />
      <span className="font-semibold">API OK</span>
      <span className="text-emerald-700">v{health.version}</span>
    </div>
  );
}
