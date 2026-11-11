/**
 * Formats a byte count into a readable human unit (e.g., 1.5 MB).
 */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "—";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB", "TB"] as const;
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const unit = units[unitIndex] ?? "TB";
  const formatted = value >= 10 ? value.toFixed(1) : value.toFixed(2);
  return `${formatted.replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1")} ${unit}`;
}

/**
 * Formats page_count while explicitly preserving 0 as "0" and null as "—".
 */
export function formatPageCount(pageCount: number | null): string {
  if (pageCount === null || pageCount === undefined) {
    return "—";
  }
  return String(pageCount);
}

/**
 * Formats chunk_count as a numeric string.
 */
export function formatChunkCount(chunkCount: number): string {
  return String(chunkCount);
}

/**
 * Truncates a SHA-256 hex digest for compact table display.
 */
export function truncateSha256(hash: string, prefixLength = 12): string {
  if (hash.length <= prefixLength) {
    return hash;
  }
  return `${hash.slice(0, prefixLength)}…`;
}

/**
 * Formats an ISO-8601 timestamp deterministically in UTC.
 */
export function formatUtcTimestamp(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return isoString;
  }
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  const seconds = String(date.getUTCSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds} UTC`;
}
