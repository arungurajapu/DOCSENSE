import { formatFileSize } from "./format";

export interface FileValidationResult {
  valid: boolean;
  error: string | null;
}

/**
 * Checks whether a filename ends with .pdf (case-insensitive).
 */
export function isPdfFilename(filename: string): boolean {
  return filename.trim().toLowerCase().endsWith(".pdf");
}

/**
 * Performs client-side pre-validation for PDF suffix, non-empty size, and max size limit.
 */
export function validatePdfFile(
  file: File,
  maxSizeBytes: number,
): FileValidationResult {
  if (!isPdfFilename(file.name)) {
    return {
      valid: false,
      error: `Only PDF files (.pdf) are supported. Selected "${file.name}".`,
    };
  }

  if (file.size <= 0) {
    return {
      valid: false,
      error: `Selected file "${file.name}" is empty (0 bytes). Please choose a non-empty PDF.`,
    };
  }

  if (file.size > maxSizeBytes) {
    return {
      valid: false,
      error: `File "${file.name}" (${formatFileSize(file.size)}) exceeds the maximum upload limit of ${formatFileSize(maxSizeBytes)}.`,
    };
  }

  return { valid: true, error: null };
}
