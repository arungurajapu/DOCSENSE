const DEFAULT_API_BASE_URL = "http://localhost:8000";
const DEFAULT_MAX_UPLOAD_SIZE_MB = 25;
const BYTES_PER_MB = 1024 * 1024;

export const POLL_INTERVAL_MS = 2000;
export const DEFAULT_PAGE_SIZE = 20;

/**
 * Resolves the backend API base URL from NEXT_PUBLIC_API_BASE_URL,
 * stripping trailing slashes and defaulting to http://localhost:8000.
 */
export function getApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!configured) {
    return DEFAULT_API_BASE_URL;
  }
  return configured.replace(/\/+$/, "");
}

/**
 * Resolves the configured max PDF upload size in MiB (default 25).
 */
export function getMaxUploadSizeMb(): number {
  const raw = process.env.NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB?.trim();
  if (!raw) {
    return DEFAULT_MAX_UPLOAD_SIZE_MB;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_MAX_UPLOAD_SIZE_MB;
  }
  return parsed;
}

/**
 * Returns the maximum upload size in bytes.
 */
export function getMaxUploadSizeBytes(): number {
  return Math.floor(getMaxUploadSizeMb() * BYTES_PER_MB);
}
