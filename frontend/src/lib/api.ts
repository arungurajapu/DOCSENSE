import type {
  DocumentListResponse,
  DocumentResponse,
  ErrorResponse,
  HealthResponse,
  ListDocumentsParams,
} from "@/types/api";
import { getApiBaseUrl } from "./config";

/**
 * Typed error raised by the DocSense API client for HTTP error responses,
 * malformed payloads, and network connectivity failures.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly isNetworkError: boolean;

  constructor(params: {
    status: number;
    code: string;
    message: string;
    isNetworkError?: boolean;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.isNetworkError = params.isNetworkError ?? false;
  }
}

/**
 * Checks whether a caught error represents an aborted fetch request.
 */
export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") {
    return true;
  }
  return error instanceof Error && error.name === "AbortError";
}

function isErrorResponsePayload(value: unknown): value is ErrorResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const envelope = value as Record<string, unknown>;
  if (typeof envelope.error !== "object" || envelope.error === null) {
    return false;
  }
  const detail = envelope.error as Record<string, unknown>;
  return typeof detail.code === "string" && typeof detail.message === "string";
}

async function parseErrorResponse(response: Response): Promise<ApiError> {
  let rawBody = "";
  try {
    rawBody = await response.text();
  } catch {
    rawBody = "";
  }

  if (rawBody.trim()) {
    try {
      const parsed: unknown = JSON.parse(rawBody);
      if (isErrorResponsePayload(parsed)) {
        return new ApiError({
          status: response.status,
          code: parsed.error.code,
          message: parsed.error.message,
        });
      }
    } catch {
      // Fall back to HTTP status message below when error body is not valid JSON.
    }
  }

  const fallbackMessage = response.statusText
    ? `Request failed (${response.status} ${response.statusText}).`
    : `Request failed with status ${response.status}.`;

  return new ApiError({
    status: response.status,
    code: `HTTP_${response.status}`,
    message: fallbackMessage,
  });
}

async function parseJsonPayload<T>(response: Response): Promise<T> {
  let rawBody = "";
  try {
    rawBody = await response.text();
  } catch {
    throw new ApiError({
      status: response.status,
      code: "INVALID_RESPONSE",
      message: "Failed to read response payload from the server.",
    });
  }

  if (!rawBody.trim()) {
    throw new ApiError({
      status: response.status,
      code: "INVALID_RESPONSE",
      message: "Server returned an empty JSON response.",
    });
  }

  try {
    return JSON.parse(rawBody) as T;
  } catch {
    throw new ApiError({
      status: response.status,
      code: "INVALID_RESPONSE",
      message: "Server returned malformed JSON.",
    });
  }
}

async function executeFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = `${getApiBaseUrl()}${path}`;
  try {
    return await fetch(url, init);
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new ApiError({
      status: 0,
      code: "NETWORK_ERROR",
      message:
        "Unable to reach the DocSense backend API. Please verify the server is running.",
      isNetworkError: true,
    });
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await executeFetch(path, init);
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
  return parseJsonPayload<T>(response);
}

/**
 * Fetches backend health and readiness status (GET /api/v1/health).
 */
export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/v1/health", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
}

/**
 * Lists documents with optional lifecycle status filter and pagination (GET /api/v1/documents).
 * Omits the `status` query parameter when filter is "all" or unset.
 */
export async function listDocuments(
  params?: ListDocumentsParams,
  signal?: AbortSignal,
): Promise<DocumentListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status && params.status !== "all") {
    searchParams.set("status", params.status);
  }
  if (params?.limit !== undefined) {
    searchParams.set("limit", String(params.limit));
  }
  if (params?.offset !== undefined) {
    searchParams.set("offset", String(params.offset));
  }

  const query = searchParams.toString();
  const path = query ? `/api/v1/documents?${query}` : "/api/v1/documents";

  return requestJson<DocumentListResponse>(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
}

/**
 * Fetches a single document by UUID (GET /api/v1/documents/{document_id}).
 */
export async function getDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  return requestJson<DocumentResponse>(
    `/api/v1/documents/${encodeURIComponent(documentId)}`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    },
  );
}

/**
 * Uploads a PDF file for asynchronous ingestion (POST /api/v1/documents -> 202).
 */
export async function uploadDocument(
  file: File,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);

  return requestJson<DocumentResponse>("/api/v1/documents", {
    method: "POST",
    headers: { Accept: "application/json" },
    body: formData,
    signal,
  });
}

/**
 * Triggers reprocessing for a failed document (POST /api/v1/documents/{document_id}/reprocess -> 202).
 */
export async function reprocessDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  return requestJson<DocumentResponse>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/reprocess`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    },
  );
}

/**
 * Deletes a document and its indexed chunks (DELETE /api/v1/documents/{document_id} -> 204).
 * Never calls response.json() on 204 No Content.
 */
export async function deleteDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<void> {
  const response = await executeFetch(
    `/api/v1/documents/${encodeURIComponent(documentId)}`,
    {
      method: "DELETE",
      headers: { Accept: "application/json" },
      signal,
    },
  );

  if (response.status === 204 || response.ok) {
    return;
  }

  throw await parseErrorResponse(response);
}
