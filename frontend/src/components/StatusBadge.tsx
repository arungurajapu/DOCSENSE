import type { DocumentStatus } from "@/types/api";

interface StatusConfig {
  label: string;
  badgeClass: string;
  dotClass: string;
}

const STATUS_STYLE_MAP: Record<DocumentStatus, StatusConfig> = {
  pending: {
    label: "Pending",
    badgeClass:
      "border-amber-200 bg-amber-50 text-amber-800",
    dotClass: "bg-amber-500",
  },
  processing: {
    label: "Processing",
    badgeClass:
      "border-sky-200 bg-sky-50 text-sky-800",
    dotClass: "bg-sky-500 animate-pulse",
  },
  ready: {
    label: "Ready",
    badgeClass:
      "border-emerald-200 bg-emerald-50 text-emerald-800",
    dotClass: "bg-emerald-500",
  },
  failed: {
    label: "Failed",
    badgeClass:
      "border-rose-200 bg-rose-50 text-rose-800",
    dotClass: "bg-rose-500",
  },
  deleting: {
    label: "Deleting",
    badgeClass:
      "border-slate-300 bg-slate-100 text-slate-700",
    dotClass: "bg-slate-400 animate-pulse",
  },
};

export interface StatusBadgeProps {
  status: DocumentStatus;
}

/**
 * Displays a document's ingestion lifecycle status pill.
 */
export function StatusBadge({ status }: StatusBadgeProps) {
  const style = STATUS_STYLE_MAP[status];

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${style.badgeClass}`}
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${style.dotClass}`}
      />
      <span>{style.label}</span>
    </span>
  );
}
