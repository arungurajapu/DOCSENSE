import type { components, operations } from "./openapi.generated";

export type DocumentStatus = components["schemas"]["DocumentStatus"];
export type DocumentResponse = components["schemas"]["DocumentResponse"];
export type DocumentListResponse =
  components["schemas"]["DocumentListResponse"];
export type HealthResponse = components["schemas"]["HealthResponse"];
export type ErrorDetail = components["schemas"]["ErrorDetail"];
export type ErrorResponse = components["schemas"]["ErrorResponse"];

export type DocumentStatusFilter = DocumentStatus | "all";

export type OpenApiListDocumentsQuery = NonNullable<
  operations["list_documents"]["parameters"]["query"]
>;

export interface ListDocumentsParams {
  status?: DocumentStatusFilter | null;
  limit?: OpenApiListDocumentsQuery["limit"];
  offset?: OpenApiListDocumentsQuery["offset"];
}

export const DOCUMENT_STATUS_VALUES: readonly DocumentStatus[] = [
  "pending",
  "processing",
  "ready",
  "failed",
  "deleting",
] as const;

export const DOCUMENT_FILTER_TABS: readonly DocumentStatusFilter[] = [
  "all",
  "pending",
  "processing",
  "ready",
  "failed",
] as const;

/**
 * Returns true if the document is in an active non-terminal ingestion state.
 */
export function isActiveIngestionStatus(status: DocumentStatus): boolean {
  return status === "pending" || status === "processing";
}
