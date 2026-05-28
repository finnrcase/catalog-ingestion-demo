"use client";

import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Copy,
  Download,
  FileText,
  ImageIcon,
  Maximize2,
  Loader2,
  Phone,
  RefreshCw,
  Settings,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  createPreferredWebsite,
  deletePreferredWebsite,
  exportProgramaCsv,
  exportProgramaXlsx,
  exportProgramaXlsxWithImages,
  exportProgramaZip,
  cancelPdfParseJob,
  fetchHealth,
  fetchPdfParseJob,
  fetchPdfParseLogs,
  fetchPreferredWebsites,
  fetchSchema,
  fetchVendorCallStatus,
  enrichRows,
  generateIntakeTable,
  generateVendorCallScript,
  recoverImages,
  refreshVendorCall,
  retryMissingData,
  retryPdfParseJob,
  startVendorCall,
  updatePreferredWebsite,
  uploadPdfForParsing,
  uploadImage,
  validateProgramaExport,
  validateRows,
} from "@/lib/api";
import { hasComplete3dDimensions } from "@/lib/dimensions";
import type { IntakeResponse, IntakeRow, PdfParseJob, PhotoDiscoveryReport, PreferredWebsiteEntry } from "@/lib/types";

const reviewColumns = [
  "Include",
  "Confidence Score",
  "Review Required",
  "Suggested Action",
  "Project",
  "Room",
  "Product Name",
  "Brand",
  "Dimensions",
  "Quantity",
  "Supplier",
  "Finish / Color",
  "Product Category",
  "Model/SKU",
  "Product URL",
  "Image URL",
  "Image Upload Status",
  "Notes",
  "Status",
];

const callFieldColumns = ["Product Name", "Brand", "Dimensions", "Quantity", "Supplier", "Room", "Product Category"];

const callFieldLabels: Record<string, string> = {
  Room: "Location",
  "Product Category": "Category",
};

const missingFieldKeys: Record<string, string> = {
  "Product Name": "Product Name",
  Brand: "Brand",
  Dimensions: "Dimensions",
  Quantity: "Quantity",
  "Image URL": "Image URL",
  Supplier: "Supplier",
  Location: "Room",
  Category: "Product Category",
};

const websiteThemeOptions = [
  { id: "light", label: "Light", description: "Bright, warm workspace for everyday use." },
  { id: "dark", label: "Dark", description: "Low-light workspace with the same clean structure." },
] as const;

const accentColorOptions = [
  { id: "orange", label: "Orange", color: "rgb(249 115 22)" },
  { id: "sage", label: "Sage", color: "rgb(95 122 101)" },
  { id: "blue", label: "Blue", color: "rgb(37 99 235)" },
  { id: "plum", label: "Plum", color: "rgb(147 51 120)" },
  { id: "mustard", label: "Mustard", color: "rgb(161 125 35)" },
  { id: "terracotta", label: "Terracotta", color: "rgb(183 101 73)" },
  { id: "slate-blue", label: "Slate Blue", color: "rgb(93 111 140)" },
  { id: "sand", label: "Sand", color: "rgb(182 154 114)" },
  { id: "forest", label: "Forest", color: "rgb(66 97 79)" },
  { id: "ocean", label: "Ocean", color: "rgb(47 111 128)" },
  { id: "clay", label: "Clay", color: "rgb(166 83 58)" },
  { id: "rosewood", label: "Rosewood", color: "rgb(138 79 94)" },
] as const;

type WebsiteThemeId = (typeof websiteThemeOptions)[number]["id"];
type AccentColorId = (typeof accentColorOptions)[number]["id"];
type UiDensityId = "comfortable" | "compact";
type AnimationPreferenceId = "smooth" | "reduced";
type EnrichmentPriorityId = "balanced" | "images" | "dimensions" | "speed";
type MissingFieldRetryModeId = "off" | "conservative" | "balanced" | "aggressive";
type MeasurementUnitId = "imperial" | "metric";

const themePreviewPalettes: Record<WebsiteThemeId, { background: string; surface: string; border: string; text: string; muted: string }> = {
  light: {
    background: "#fafaf8",
    surface: "#ffffff",
    border: "#e5e7eb",
    text: "#1f2933",
    muted: "#6b7280",
  },
  dark: {
    background: "#151619",
    surface: "#202225",
    border: "#4c525b",
    text: "#f2f4f7",
    muted: "#b5bcc6",
  },
};

const websiteThemeStorageKey = "sch:websiteTheme";
const accentColorStorageKey = "sch:accentColor";
const uiDensityStorageKey = "sch:uiDensity";
const animationPreferenceStorageKey = "sch:animationPreference";
const enrichmentPriorityStorageKey = "sch:enrichmentPriority";
const missingFieldRetryModeStorageKey = "sch:missingFieldRetryMode";
const missingFieldRetryRunCostStorageKey = "sch:missingFieldRetryRunCost";
const missingFieldRetryItemCostStorageKey = "sch:missingFieldRetryItemCost";
const replaceLowConfidenceStorageKey = "sch:replaceLowConfidenceData";
const autoRetryStorageKey = "sch:autoRetryFailed";
const measurementUnitStorageKey = "sch:measurementUnit";
const itemDebugModeStorageKey = "sch:itemDebugMode";

const uiDensityOptions: { id: UiDensityId; label: string; description: string }[] = [
  { id: "comfortable", label: "Comfortable", description: "More breathing room for review work." },
  { id: "compact", label: "Compact", description: "Tighter rows and controls for dense lists." },
];

const animationPreferenceOptions: { id: AnimationPreferenceId; label: string; description: string }[] = [
  { id: "smooth", label: "Smooth", description: "Keep subtle interface motion." },
  { id: "reduced", label: "Reduced", description: "Minimize transitions and motion." },
];

const enrichmentPriorityOptions: { id: EnrichmentPriorityId; label: string; description: string }[] = [
  { id: "balanced", label: "Balanced", description: "Keep image, dimension, and speed needs even." },
  { id: "images", label: "Prioritize images", description: "Put missing product images first during review." },
  { id: "dimensions", label: "Prioritize dimensions", description: "Put missing measurements first during review." },
  { id: "speed", label: "Prioritize speed", description: "Favor quicker passes and fewer deep checks." },
];

const missingFieldRetryModeOptions: { id: MissingFieldRetryModeId; label: string; description: string }[] = [
  { id: "off", label: "Off", description: "Run only the first cheap enrichment pass." },
  { id: "conservative", label: "Conservative", description: "Reuse verified pages first, with one tiny targeted retry when budget allows." },
  { id: "balanced", label: "Balanced", description: "Allow a little more targeted work for missing dimensions and images." },
  { id: "aggressive", label: "Aggressive", description: "Internal/manual mode for stubborn rows; still budget capped." },
];

const missingFieldPlaceholders: Record<string, string> = {
  "Product Name": "Enter product name",
  Brand: "Enter manufacturer / brand",
  Dimensions: "Enter full W x H x D dimensions",
  Quantity: "Enter quantity",
  "Image URL": "Paste hosted image URL",
  Supplier: "Enter supplier",
  Location: "Enter location",
  Category: "Enter category",
};

const fallbackSections = [
  "Appliances",
  "Lighting",
  "Plumbing",
  "Cabinetry",
  "Flooring",
  "Furniture",
  "Decor",
  "Hardware",
  "Exterior",
  "General",
];

const INTERNAL_DEBUG_ENABLED =
  process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_INTERNAL_DEBUG === "true";

function rowText(row: IntakeRow, key: string) {
  return String(row[key] ?? "");
}

function isPhotoOnlyRow(row: IntakeRow) {
  const photoOnly = rowText(row, "photo_only").trim().toLowerCase();
  const sourceType = rowText(row, "Source Type").trim();
  const importType = rowText(row, "Import Type").trim().toLowerCase();
  return photoOnly === "true" || photoOnly === "1" || sourceType === "Photo" || importType === "photo-only bulk import";
}

function isPublicHttpsImageUrl(value: string) {
  return value.trim().toLowerCase().startsWith("https://");
}

function domainFromUrl(value: string) {
  if (!value) return "";
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function photoReportFromDiagnostics(diagnostics?: Record<string, unknown>[]): PhotoDiscoveryReport | null {
  const item = diagnostics?.find((entry) => entry.report_type === "photo_discovery");
  const summary = item?.summary;
  if (!summary || typeof summary !== "object") return null;
  return summary as PhotoDiscoveryReport;
}

function filenameStem(filename: string) {
  const withoutExtension = filename.replace(/\.[^/.]+$/, "").trim();
  return withoutExtension || "photo_item";
}

function buildPhotoOnlyName(filename: string, index: number, defaultName: string, appendSequence: boolean) {
  const baseName = defaultName.trim();
  if (!baseName) return filenameStem(filename);
  return appendSequence ? `${baseName} ${String(index + 1).padStart(3, "0")}` : baseName;
}

function bulkImageKey(file: File, index: number) {
  return `${index}:${file.name}:${file.size}:${file.lastModified}`;
}

type UploadFileKind = "pdf" | "image" | "unsupported";

type UploadDebugInfo = {
  fileName: string;
  fileType: string;
  fileSize: string;
  failedStep: string;
  errorMessage: string;
  suggestedFix: string;
  parserAttempted?: string;
  timeoutReason?: string;
  stackTraceSnippet?: string;
  rawLogs?: string;
  retryJobId?: string;
};

type DebugUploadSnapshot = {
  fileName: string;
  fileType: string;
  fileSize: string;
  uploadedAt: string;
  parser: string;
  parseDurationMs: number;
  pageCount: number | null;
  ocrUsed: boolean;
  rawTextLength: number;
  detectedItemGroups: number;
  finalProductEntries: number;
  logs?: Record<string, unknown>[] | null;
  telemetry?: Record<string, unknown>;
};

type ImagePreviewState = {
  url: string;
  productName: string;
  sourcePage: string;
  sourceDomain: string;
  sourceLabel: string;
  resolution: string;
};

const UPLOAD_TIMEOUT_MS = 90_000;

function classifyUploadFile(file: File): UploadFileKind {
  const type = file.type.toLowerCase();
  const name = file.name.toLowerCase();
  if (type === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (type.startsWith("image/") || /\.(jpe?g|png|webp|gif|heic|heif)$/.test(name)) return "image";
  return "unsupported";
}

function uploadFileSummary(filesToSummarize: File[]) {
  if (!filesToSummarize.length) return "";
  if (filesToSummarize.length === 1) return filesToSummarize[0].name;
  return `${filesToSummarize[0].name} + ${filesToSummarize.length - 1} more`;
}

function uploadFileTypeSummary(filesToSummarize: File[]) {
  const types = filesToSummarize.map((file) => file.type || "unknown").filter(Boolean);
  return Array.from(new Set(types)).join(", ") || "unknown";
}

function suggestedUploadFix(kind: UploadFileKind, message: string) {
  const lower = message.toLowerCase();
  if (kind === "unsupported") return "Upload a PDF, JPG, PNG, or WebP file.";
  if (kind === "image" && lower.includes("cloudinary")) return "Check Cloudinary settings, then retry the image upload.";
  if (kind === "image") return "Try a JPG, PNG, or WebP image under the upload size limit.";
  if (lower.includes("backend")) return "The backend may be waking up. Wait a moment, then retry parsing without reuploading.";
  if (lower.includes("timed out") || lower.includes("timeout")) return "Parsing is taking longer than expected. Retry with the fallback parser from diagnostics.";
  return "Retry parsing. The uploaded file is preserved, so you should not need to reupload.";
}

function logUploadStage(stage: "selected" | "validating" | "parsing" | "complete" | "error", detail: Record<string, unknown> = {}) {
  console.info("[upload]", { stage, ...detail });
}

function buildDefaultCallGoal(row: IntakeRow, missingFields: string[]) {
  const fieldText = missingFields.join(", ") || "details";
  const subject =
    [rowText(row, "Brand"), rowText(row, "Model/SKU")].filter(Boolean).join(" ").trim() ||
    rowText(row, "Product Name").trim() ||
    "this product";
  return `Get the missing ${fieldText} for ${subject}.`;
}

function formatFileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`;
}

function formatUsd(value: unknown) {
  const numeric = Number(value ?? 0);
  return `$${Number.isFinite(numeric) ? numeric.toFixed(4) : "0.0000"}`;
}

function optionalPositiveNumber(value: string) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : undefined;
}

function formatPercent(value: number | null) {
  return value === null ? "No data yet" : `${Math.round(value * 100)}%`;
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function pdfStageLabel(job: PdfParseJob) {
  const stage = (job.stage || job.status || "").toLowerCase();
  if (stage === "queued") return "Queued";
  if (stage === "parsing") return "Parsing";
  if (stage === "ocr_fallback") return "OCR fallback running";
  if (stage === "complete") return "Complete";
  if (stage === "failed") return "Failed";
  if (stage === "cancelled") return "Cancelled";
  return stage || "Parsing";
}

function pdfJobDebug(file: File, job: PdfParseJob, rawLogs = ""): UploadDebugInfo {
  const attempts = Array.isArray(job.telemetry?.attempts) ? job.telemetry.attempts as Record<string, unknown>[] : [];
  const parserAttempted = attempts.map((attempt) => String(attempt.parser || "")).filter(Boolean).join(" → ");
  const failedAttempt = attempts.find((attempt) => attempt.status === "timeout" || attempt.status === "failed");
  const stack = String(failedAttempt?.stack || job.telemetry?.stack || "");
  return {
    fileName: file.name,
    fileType: file.type || "application/pdf",
    fileSize: formatFileSize(file.size),
    failedStep: pdfStageLabel(job),
    errorMessage: job.errors?.join(" ") || "PDF parsing failed.",
    suggestedFix: suggestedUploadFix("pdf", job.errors?.join(" ") || ""),
    parserAttempted,
    timeoutReason: String(failedAttempt?.error || job.telemetry?.parse_status || ""),
    stackTraceSnippet: stack.slice(0, 900),
    rawLogs,
    retryJobId: job.job_id,
  };
}

function debugUploadSnapshot(file: File, job: PdfParseJob, uploadedAt: string, logs?: PdfParseJob | null): DebugUploadSnapshot {
  const attempts = Array.isArray(job.telemetry?.attempts) ? job.telemetry.attempts as Record<string, unknown>[] : [];
  const durationMs = attempts.reduce((total, attempt) => total + Math.round(Number(attempt.duration_seconds || 0) * 1000), 0);
  return {
    fileName: file.name,
    fileType: file.type || "application/pdf",
    fileSize: formatFileSize(file.size),
    uploadedAt,
    parser: String(job.telemetry?.parser_used || attempts.map((attempt) => attempt.parser).filter(Boolean).join(" -> ") || ""),
    parseDurationMs: durationMs,
    pageCount: typeof job.telemetry?.page_count === "number" ? job.telemetry.page_count : null,
    ocrUsed: Boolean(job.telemetry?.ocr_triggered),
    rawTextLength: Number(job.telemetry?.extracted_text_length || 0),
    detectedItemGroups: job.rows.filter((row) => rowText(row, "_raw_grouped_text")).length,
    finalProductEntries: job.rows.length,
    logs: logs?.logs || null,
    telemetry: job.telemetry,
  };
}

async function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function cleanNotes(value: string) {
  return value.replace(/^\s*(?:row\s*)?#?\d{1,3}\s*[-–—:.)]\s+(?=[A-Za-z\[\("'])/i, "").trim();
}

function safeParseRecord(value: string): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function safeParseArray(value: string): Record<string, unknown>[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item === "object") as Record<string, unknown>[] : [];
  } catch {
    return [];
  }
}

function candidateImageUrl(candidate: Record<string, unknown>) {
  return String(candidate.url || candidate.image_url || "").trim();
}

function itemSearchAttemptCount(row: IntakeRow) {
  const explicit = Number(row["Search Attempts"] || row["search_attempts"] || row["attempt_count"] || 0);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  return (
    safeParseArray(rowText(row, "_bravi_calls")).length +
    safeParseArray(rowText(row, "_image_candidates")).length +
    (rowText(row, "_enrichment_query_used") ? 1 : 0)
  );
}

function itemEstimatedCost(row: IntakeRow) {
  const label = rowText(row, "Bravi Cost");
  if (label) return label;
  return formatUsd(row["Bravi Cost USD"]);
}

function imageResolutionForRow(row: IntakeRow) {
  const width = Number(
    row["cloudinary_width"] ||
      row["image_width"] ||
      row["Image Width"] ||
      row["_image_width"] ||
      0,
  );
  const height = Number(
    row["cloudinary_height"] ||
      row["image_height"] ||
      row["Image Height"] ||
      row["_image_height"] ||
      0,
  );
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
    ? `${Math.round(width)} x ${Math.round(height)} px`
    : "";
}

function imagePreviewStateForRow(row: IntakeRow): ImagePreviewState | null {
  const url = rowText(row, "Image URL");
  if (!isPublicHttpsImageUrl(url)) return null;
  const sourcePage = rowText(row, "Product URL") || rowText(row, "Original Image URL");
  const sourceDomain =
    rowText(row, "Selected Source Domain") ||
    domainFromUrl(sourcePage) ||
    domainFromUrl(rowText(row, "Original Image URL")) ||
    domainFromUrl(url);
  const sourceLabel = rowText(row, "image_source") || rowText(row, "_image_source_type") || "Product image";
  return {
    url,
    productName: rowText(row, "Product Name") || "Product image",
    sourcePage,
    sourceDomain,
    sourceLabel,
    resolution: imageResolutionForRow(row),
  };
}

function itemDebugRows(row: IntakeRow) {
  const productUrl = rowText(row, "Product URL");
  const dimensionSourceUrl = rowText(row, "Dimension Source URL") || productUrl;
  const specSourceUrl = rowText(row, "Successful Source Stored") || productUrl;
  const missing = missingFieldsForDebug(row);
  return [
    ["Search source", rowText(row, "Selected Source Domain") || domainFromUrl(productUrl) || rowText(row, "Source Domains Tried") || "none"],
    ["Extraction confidence", rowText(row, "Product Resolution Confidence") || rowText(row, "confidence") || rowText(row, "Confidence Score") || "none"],
    ["Image supplied by", rowText(row, "image_source") || rowText(row, "_image_source_type") || domainFromUrl(rowText(row, "Image URL")) || "none"],
    ["Dimensions supplied by", [rowText(row, "Dimension Source Type"), domainFromUrl(dimensionSourceUrl)].filter(Boolean).join(" · ") || "none"],
    ["Specs supplied by", rowText(row, "Source Selection Reason") || domainFromUrl(specSourceUrl) || "none"],
    ["Search attempts", itemSearchAttemptCount(row)],
    ["Estimated cost", itemEstimatedCost(row)],
    ["Failure reason", rowText(row, "Suggested Action") || (missing.length ? `Missing ${missing.join(", ")}` : "none")],
  ] as const;
}

function missingFieldsForDebug(row: IntakeRow) {
  const explicit = rowText(row, "Missing Fields");
  if (explicit) return explicit.split(",").map((item) => item.trim()).filter(Boolean);
  return missingFieldsForRow(row);
}

function phoneCallFieldsForRow(row: IntakeRow) {
  const fields = new Set(missingFieldsForDebug(row));
  if (!hasComplete3dDimensions(row.Dimensions)) fields.add("Dimensions");
  if (!isPublicHttpsImageUrl(rowText(row, "Image URL"))) fields.add("Image URL");
  if (!rowText(row, "Product URL").trim()) fields.add("Product URL");
  const confidence = rowText(row, "confidence").toUpperCase();
  const needsReview =
    row["Review Required"] === true ||
    rowText(row, "Status").toLowerCase().includes("review") ||
    rowText(row, "needs_image_review").toLowerCase() === "true";
  if (needsReview || (confidence && confidence !== "HIGH")) fields.add("Spec confirmation");
  return Array.from(fields).filter(Boolean);
}

function shouldEmphasizePhoneCall(row: IntakeRow) {
  return phoneCallFieldsForRow(row).some((field) =>
    ["Dimensions", "Image URL", "Product URL", "Spec confirmation"].includes(field),
  );
}

function traceField(row: IntakeRow, field: string, parsed: Record<string, unknown>) {
  const value = rowText(row, field);
  const mappedKeys: Record<string, string> = {
    Project: "project",
    Room: "room",
    Supplier: "supplier",
    "Product Category": "category",
    Brand: "brand",
    "Model/SKU": "model",
    "Product Name": "description",
    "Finish / Color": "finish",
    Quantity: "quantity",
    Price: "price",
    Dimensions: "dimensions",
    "Image URL": "image_url",
    "Product URL": "product_url",
  };
  const parsedValue = parsed[mappedKeys[field] || field];
  const hasParsedValue = parsedValue !== undefined && String(parsedValue || "").trim() !== "";
  const grouped = Boolean(rowText(row, "_raw_grouped_text"));
  const source =
    !value
      ? "missing"
      : field === "Project" || field === "Supplier" || field === "Product Category"
        ? hasParsedValue || grouped ? "global context" : "user input"
        : ["Brand", "Model/SKU", "Product Name", "Finish / Color", "Quantity", "Price"].includes(field)
          ? hasParsedValue || grouped ? "PDF grouped text" : "user input"
          : ["Dimensions", "Image URL", "Product URL"].includes(field)
            ? "enriched"
            : "user input";
  const confidence =
    !value
      ? 0
      : Number(row["_extraction_confidence"] || row["Confidence Score"] || 0) / 100 || (source === "PDF grouped text" ? 0.85 : 0.7);
  const reason =
    !value
      ? `${field} was not found in PDF text or enrichment output.`
      : source === "PDF grouped text"
        ? rowText(row, "_confidence_reason") || `Detected ${field} inside grouped quote item text.`
        : source === "global context"
          ? `Detected from quote-level context and applied to the item.`
          : source === "enriched"
            ? `Filled after validation/enrichment or manual image recovery.`
            : `Current row value; may have been entered or edited by the user.`;
  return { value: value || null, source, confidence: Math.min(1, Math.max(0, confidence)), reason };
}

function buildInternalDebugReport(
  rows: IntakeRow[],
  uploads: DebugUploadSnapshot[],
  errors: string[],
  exportSummary: { export_count: number },
  diagnostics: Record<string, unknown>[],
) {
  const missingCounts: Record<string, number> = {};
  rows.forEach((row) => {
    missingFieldsForDebug(row).forEach((field) => {
      missingCounts[field] = (missingCounts[field] || 0) + 1;
    });
  });
  const parserWarnings = uploads.flatMap((upload) =>
    (upload.logs || [])
      .filter((entry) => ["failed", "cancelled"].includes(String(entry.stage || "")) || String(entry.message || "").toLowerCase().includes("warning"))
      .map((entry) => String(entry.message || entry.stage || "")),
  );
  const groupingWarnings = rows
    .filter((row) => rowText(row, "Source Type") === "PDF" && !rowText(row, "_raw_grouped_text"))
    .map((row, index) => `Item ${index + 1} was parsed by line/table fallback without grouped quote text.`);
  const enrichmentMetricsEntry =
    diagnostics.find((entry) => entry.report_type === "enrichment_metrics") ||
    diagnostics.find((entry) => entry.report_type === "missing_field_retry_metrics");
  const enrichmentMetrics =
    enrichmentMetricsEntry?.summary && typeof enrichmentMetricsEntry.summary === "object"
      ? enrichmentMetricsEntry.summary as Record<string, unknown>
      : null;

  const products = rows.map((row, index) => {
    const parsed = safeParseRecord(rowText(row, "_parsed_fields"));
    const fields = [
      "Project",
      "Room",
      "Supplier",
      "Product Category",
      "Brand",
      "Model/SKU",
      "Product Name",
      "Finish / Color",
      "Quantity",
      "Price",
      "Dimensions",
      "Image URL",
      "Product URL",
    ].reduce<Record<string, ReturnType<typeof traceField>>>((acc, field) => {
      acc[field] = traceField(row, field, parsed);
      return acc;
    }, {});
    const missingFields = missingFieldsForDebug(row);
    const imageCandidates = safeParseArray(rowText(row, "_image_candidates"));
    const rejectedImageCandidates = safeParseArray(rowText(row, "_image_rejected_candidates"));
    const rejectedImageText = rowText(row, "_image_rejected_candidates");
    return {
      id: rowText(row, "_item_number") || String(index + 1),
      index: index + 1,
      sourceLineNumber: row["_source_page_number"] || null,
      rawGroupedText: rowText(row, "_raw_grouped_text"),
      parsedFields: fields,
      enrichment: {
        query: rowText(row, "_enrichment_query_used") || [rowText(row, "Brand"), rowText(row, "Model/SKU"), rowText(row, "Product Name")].filter(Boolean).join(" "),
        attempted: Boolean(rowText(row, "Product URL") || rowText(row, "Dimension Lookup Status") || rowText(row, "image_source") || diagnostics.length),
        braviUsed: rowText(row, "Bravi Used") || "no",
        braviQuery: rowText(row, "Bravi Query"),
        braviCost: rowText(row, "Bravi Cost") || formatUsd(row["Bravi Cost USD"]),
        braviResultStatus: rowText(row, "Bravi Result Status"),
        braviFieldsFilled: rowText(row, "Bravi Fields Filled"),
        braviSkippedReason: rowText(row, "Bravi Skipped Reason"),
        braviCacheStatus: rowText(row, "Bravi Cache Status"),
        braviCalls: safeParseArray(rowText(row, "_bravi_calls")),
        matchedUrl: rowText(row, "Product URL") || null,
        sourceDomainsTried: rowText(row, "Source Domains Tried"),
        selectedDomain: rowText(row, "Selected Source Domain"),
        sourceSelectionReason: rowText(row, "Source Selection Reason"),
        dimensionsExtractionMethod: rowText(row, "Dimensions Extraction Method"),
        imageExtractionMethod: rowText(row, "Image Extraction Method"),
        targetedRetryMode: rowText(row, "Targeted Retry Mode"),
        targetedRetryMissingFields: rowText(row, "Targeted Retry Missing Fields"),
        targetedRetryStatus: rowText(row, "Targeted Retry Status"),
        targetedRetryFilledFields: rowText(row, "Targeted Retry Filled Fields"),
        targetedRetryExtraCost: rowText(row, "Targeted Retry Extra Cost"),
        targetedRetryAttempts: safeParseArray(rowText(row, "Targeted Retry Attempts")),
        targetedRetryFailureReason: rowText(row, "Targeted Retry Failure Reason"),
        successfulSourceStored: rowText(row, "Successful Source Stored"),
        rejectedUrlsAndReasons: rowText(row, "Rejected URLs and Reasons"),
        extractedFields: {
          dimensions: rowText(row, "Dimensions") || null,
          imageUrl: rowText(row, "Image URL") || null,
          productUrl: rowText(row, "Product URL") || null,
          material: rowText(row, "Material") || null,
          finish: rowText(row, "Finish / Color") || null,
        },
        failedFields: missingFields,
        failureReason: rowText(row, "Suggested Action") || (missingFields.length ? `Missing ${missingFields.join(", ")}` : ""),
        retryCount: Number(row["retry_count"] || 0),
        status: rowText(row, "Status"),
      },
      imageTrace: {
        queryUsed: rowText(row, "_image_query_used"),
        candidatesFound: imageCandidates,
        selectedCandidate: rowText(row, "_selected_image_candidate") || rowText(row, "Image URL") || null,
        rejectedCandidates: rejectedImageCandidates.length ? rejectedImageCandidates : rejectedImageText,
        sourceType: rowText(row, "_image_source_type") || rowText(row, "image_source"),
        finalConfidence: rowText(row, "_image_final_confidence") || rowText(row, "confidence"),
        uploadStatus: rowText(row, "image_upload_status") || rowText(row, "Image Upload Status"),
        uploadFailureReason: rowText(row, "image_upload_failure_reason"),
        uploadDebug: safeParseRecord(rowText(row, "_image_upload_debug")),
        cloudinaryUrl: rowText(row, "cloudinary_secure_url"),
        cloudinaryPublicId: rowText(row, "cloudinary_public_id"),
        originalImageUrl: rowText(row, "Original Image URL"),
      },
      finalStatus: rowText(row, "Status"),
      missingFields,
      confidenceScore: Number(row["Confidence Score"] || 0),
      confidenceReasons: rowText(row, "_confidence_reason") || rowText(row, "Suggested Action"),
    };
  });

  return {
    generatedAt: new Date().toISOString(),
    upload: uploads[uploads.length - 1] || null,
    uploads,
    summary: {
      totalFinalProductEntries: rows.length,
      numberReady: exportSummary.export_count,
      numberNeedsReview: rows.filter((row) => row["Review Required"] === true || rowText(row, "Status").toLowerCase().includes("review")).length,
    },
    failureSummary: {
      fieldsMostCommonlyMissing: Object.entries(missingCounts).sort((a, b) => b[1] - a[1]).map(([field, count]) => ({ field, count })),
      itemsThatFailedEnrichment: products
        .filter((product) => product.missingFields.length > 0)
        .map((product) => ({ index: product.index, id: product.id, missingFields: product.missingFields, reason: product.enrichment.failureReason })),
      parserWarnings,
      groupingWarnings,
      skippedRows: "Header/junk/warranty rows are filtered during grouping and are not emitted as products.",
      duplicateOrLowConfidenceItems: rows
        .map((row, index) => ({ index: index + 1, confidenceScore: Number(row["Confidence Score"] || 0), productName: rowText(row, "Product Name") }))
        .filter((item) => item.confidenceScore > 0 && item.confidenceScore < 75),
    },
    enrichmentMetrics,
    extraction: {
      rawTextPreview: products.map((product) => product.rawGroupedText).filter(Boolean).join("\n---\n").slice(0, 4000),
      detectedGlobals: uploads[uploads.length - 1] ? {
        fileName: uploads[uploads.length - 1].fileName,
        parser: uploads[uploads.length - 1].parser,
      } : {},
      itemGroups: products.map((product) => ({ id: product.id, rawGroupedText: product.rawGroupedText, parsedFields: product.parsedFields })),
      skippedRows: "Filtered internally: quote headers, column headers, warranty rows, contact/address lines, totals/subtotals.",
      warnings: [...parserWarnings, ...groupingWarnings],
    },
    products,
    errors,
    diagnostics,
  };
}

function formatDebugReportText(report: ReturnType<typeof buildInternalDebugReport>) {
  const lines = [
    "SCH DesignOps Intake Debug Report",
    `Generated: ${report.generatedAt}`,
    "",
    "Upload Summary",
  ];
  if (report.upload) {
    lines.push(
      `File: ${report.upload.fileName}`,
      `Type: ${report.upload.fileType}`,
      `Size: ${report.upload.fileSize}`,
      `Uploaded At: ${report.upload.uploadedAt}`,
      `Parser: ${report.upload.parser || "unknown"}`,
      `Parse Duration: ${report.upload.parseDurationMs}ms`,
      `Page Count: ${report.upload.pageCount ?? "unknown"}`,
      `OCR Used: ${report.upload.ocrUsed ? "yes" : "no"}`,
      `Raw Text Length: ${report.upload.rawTextLength}`,
      `Detected Item Groups: ${report.upload.detectedItemGroups}`,
      `Final Product Entries: ${report.upload.finalProductEntries}`,
    );
  } else {
    lines.push("No upload telemetry captured in this browser session.");
  }
  lines.push(
    `Ready: ${report.summary.numberReady}`,
    `Needs Review: ${report.summary.numberNeedsReview}`,
  );
  if (report.enrichmentMetrics) {
    const metrics = report.enrichmentMetrics;
    lines.push(
      "",
      "Enrichment Metrics",
      `Mode: ${metrics.mode ?? "unknown"}`,
      `Target Budget: ${formatUsd(metrics.target_budget_usd)}`,
      `Hard Budget: ${formatUsd(metrics.hard_budget_usd)}`,
      `Estimated Cost: ${formatUsd(metrics.estimated_cost_usd)}`,
      `Bravi API Cost: ${formatUsd(metrics.bravi_cost_usd)}`,
      `Bravi Searches: ${metrics.bravi_searches ?? 0}`,
      `Average Cost Per Item: ${formatUsd(metrics.avg_cost_per_item_usd)}`,
      `Cache Hits vs Paid Calls: ${metrics.cache_hits ?? 0}/${metrics.paid_calls ?? 0}`,
      `Remaining Budget: ${formatUsd(metrics.remaining_budget_usd)}`,
      `Search Calls: ${metrics.search_calls ?? 0}`,
      `Page Fetches: ${metrics.page_fetches ?? 0}`,
      `External Lookups: ${metrics.external_lookups ?? 0}/${metrics.external_lookups_limit ?? "?"}`,
      `Image Searches: ${metrics.image_searches ?? 0}/${metrics.image_searches_limit ?? "?"}`,
      `Broad Searches: ${metrics.broad_searches ?? 0}`,
      `Retries: ${metrics.retries ?? 0}/${metrics.retries_limit ?? "?"}`,
      `AI Calls: ${metrics.ai_calls ?? 0}`,
      `AI Call Limit: ${metrics.ai_calls_limit ?? "?"}`,
      `AI Calls Avoided: ${metrics.ai_calls_avoided ?? 0}`,
      `Cache Hit Rate: ${metrics.cache_hit_rate ?? 0}`,
      `Cache Hits: ${metrics.cache_hits ?? 0}`,
      `Duplicate Reuse: ${metrics.duplicate_reuse ?? 0}`,
      `Cheap Local Only: ${metrics.cheap_local_only ?? 0}`,
      `Targeted Retry Mode: ${metrics.targeted_retry_mode ?? "conservative"}`,
      `Targeted Retry Items: ${metrics.targeted_retry_items ?? 0}`,
      `Targeted Retry Attempts: ${metrics.targeted_retry_attempts ?? 0}`,
      `Targeted Retry Fields Filled: ${metrics.targeted_retry_fields_filled ?? 0}`,
      `Targeted Retry Extra Cost: ${formatUsd(metrics.targeted_retry_extra_cost_usd)}`,
      `Skipped Enrichments: ${metrics.skipped_enrichments ?? 0}`,
      `Budget-Skipped Calls: ${metrics.skipped_calls_due_budget ?? 0}`,
      `Budget-Skipped Fields: ${metrics.fields_skipped_due_budget ?? 0}`,
      `Most Expensive Item: ${metrics.most_expensive_item || "none"} (${formatUsd(metrics.most_expensive_item_cost_usd)})`,
      `Cost By Stage: ${JSON.stringify(metrics.cost_by_stage || {})}`,
      `Cost By Provider: ${JSON.stringify(metrics.cost_by_provider || {})}`,
      `Cost By Field: ${JSON.stringify(metrics.cost_by_field || {})}`,
      `Bravi Calls: ${JSON.stringify(metrics.bravi_calls || [])}`,
      `Paid Call Reasons: ${JSON.stringify(metrics.paid_call_reasons || [])}`,
      `Budget Skipped Calls: ${JSON.stringify(metrics.budget_skipped_calls || [])}`,
      `Budget Skipped Fields: ${JSON.stringify(metrics.budget_skipped_fields || [])}`,
      `Duration: ${metrics.duration_ms ?? 0}ms`,
    );
  }
  lines.push(
    "",
    "Failure Summary",
    `Missing Fields: ${report.failureSummary.fieldsMostCommonlyMissing.map((item) => `${item.field} (${item.count})`).join(", ") || "none"}`,
    `Parser Warnings: ${report.failureSummary.parserWarnings.join(" | ") || "none"}`,
    `Grouping Warnings: ${report.failureSummary.groupingWarnings.join(" | ") || "none"}`,
    "",
    "Products",
  );
  report.products.forEach((product) => {
    lines.push(
      "",
      `#${product.index} ${product.parsedFields["Product Name"].value || "Unnamed Item"}`,
      `Source Line/Page: ${product.sourceLineNumber ?? "unknown"}`,
      `Raw Grouped Text:\n${product.rawGroupedText || "(none)"}`,
      "Parsed Fields:",
    );
    Object.entries(product.parsedFields).forEach(([field, trace]) => {
      lines.push(`- ${field}: ${trace.value ?? "null"} | source=${trace.source} | confidence=${trace.confidence} | reason=${trace.reason}`);
    });
    lines.push(
      "Enrichment:",
      `- Query: ${product.enrichment.query || "(none)"}`,
      `- Attempted: ${product.enrichment.attempted ? "yes" : "no"}`,
      `- Matched URL: ${product.enrichment.matchedUrl || "none"}`,
      `- Source Domains Tried: ${product.enrichment.sourceDomainsTried || "none"}`,
      `- Selected Domain: ${product.enrichment.selectedDomain || "none"}`,
      `- Source Selection Reason: ${product.enrichment.sourceSelectionReason || "none"}`,
      `- Dimensions Method: ${product.enrichment.dimensionsExtractionMethod || "none"}`,
      `- Image Method: ${product.enrichment.imageExtractionMethod || "none"}`,
      `- Targeted Retry: ${product.enrichment.targetedRetryStatus || "none"}`,
      `- Retry Missing Fields: ${product.enrichment.targetedRetryMissingFields || "none"}`,
      `- Retry Filled Fields: ${product.enrichment.targetedRetryFilledFields || "none"}`,
      `- Retry Extra Cost: ${product.enrichment.targetedRetryExtraCost || "$0.0000"}`,
      `- Retry Attempts: ${JSON.stringify(product.enrichment.targetedRetryAttempts || [])}`,
      `- Successful Source Stored: ${product.enrichment.successfulSourceStored || "none"}`,
      `- Rejected URLs: ${product.enrichment.rejectedUrlsAndReasons || "none"}`,
      `- Failed Fields: ${product.enrichment.failedFields.join(", ") || "none"}`,
      `- Failure Reason: ${product.enrichment.failureReason || "none"}`,
      `- Retry Count: ${product.enrichment.retryCount}`,
      `- Final Status: ${product.finalStatus}`,
      `- Confidence: ${product.confidenceScore}`,
      `- Confidence Reason: ${product.confidenceReasons || "none"}`,
      "Bravi API Trace:",
      `- Used: ${product.enrichment.braviUsed || "no"}`,
      `- Query: ${product.enrichment.braviQuery || "none"}`,
      `- Cost: ${product.enrichment.braviCost || "$0.0000"}`,
      `- Status: ${product.enrichment.braviResultStatus || "none"}`,
      `- Fields Filled: ${product.enrichment.braviFieldsFilled || "none"}`,
      `- Cache/Budget: ${product.enrichment.braviCacheStatus || "none"} ${product.enrichment.braviSkippedReason || ""}`.trim(),
      `- Calls: ${JSON.stringify(product.enrichment.braviCalls || [])}`,
      "Image Trace:",
      `- Query Used: ${product.imageTrace.queryUsed || "none"}`,
      `- Selected: ${product.imageTrace.selectedCandidate || "none"}`,
      `- Source: ${product.imageTrace.sourceType || "none"}`,
      `- Confidence: ${product.imageTrace.finalConfidence || "none"}`,
      `- Upload Status: ${product.imageTrace.uploadStatus || "none"}`,
      `- Upload Failure: ${product.imageTrace.uploadFailureReason || "none"}`,
      `- Cloudinary URL: ${product.imageTrace.cloudinaryUrl || "none"}`,
      `- Cloudinary Public ID: ${product.imageTrace.cloudinaryPublicId || "none"}`,
      `- Original Image URL: ${product.imageTrace.originalImageUrl || "none"}`,
      `- Upload Debug: ${JSON.stringify(product.imageTrace.uploadDebug)}`,
      `- Candidates: ${JSON.stringify(product.imageTrace.candidatesFound)}`,
      `- Rejected: ${typeof product.imageTrace.rejectedCandidates === "string" ? product.imageTrace.rejectedCandidates || "none" : JSON.stringify(product.imageTrace.rejectedCandidates)}`,
    );
  });
  return lines.join("\n");
}

function isEligible(row: IntakeRow) {
  if (row.Include === false) return false;
  if (row["Review Required"] === true) return false;
  if (["Ignored", "Excluded", "Error"].includes(rowText(row, "Status"))) return false;
  if (isPhotoOnlyRow(row)) {
    return (
      Boolean(rowText(row, "Product Name").trim()) &&
      Boolean(rowText(row, "Product Category").trim()) &&
      Number(row.Quantity ?? 0) >= 1 &&
      isPublicHttpsImageUrl(rowText(row, "Image URL"))
    );
  }
  if (!rowText(row, "Product Name").trim()) return false;
  if (!rowText(row, "Brand").trim()) return false;
  if (!rowText(row, "Product Category").trim()) return false;
  if (!rowText(row, "Supplier").trim()) return false;
  if (!rowText(row, "Room").trim()) return false;
  if (!hasComplete3dDimensions(row.Dimensions)) return false;
  const qty = Number(row.Quantity ?? 0);
  return Number.isFinite(qty) && qty >= 1;
}

function missingFieldsForRow(row: IntakeRow) {
  const missing: string[] = [];
  if (isPhotoOnlyRow(row)) {
    if (!rowText(row, "Product Name").trim()) missing.push("Product Name");
    if (!rowText(row, "Product Category").trim()) missing.push("Category");
    const qty = Number(row.Quantity ?? 0);
    if (!Number.isFinite(qty) || qty < 1) missing.push("Quantity");
    if (!isPublicHttpsImageUrl(rowText(row, "Image URL"))) missing.push("Image URL");
    return missing;
  }
  if (!rowText(row, "Product Name").trim()) missing.push("Product Name");
  if (!rowText(row, "Brand").trim()) missing.push("Brand");
  if (!hasComplete3dDimensions(row.Dimensions)) missing.push("Dimensions");
  const qty = Number(row.Quantity ?? 0);
  if (!Number.isFinite(qty) || qty < 1) missing.push("Quantity");
  if (!rowText(row, "Supplier").trim()) missing.push("Supplier");
  if (!rowText(row, "Room").trim()) missing.push("Location");
  if (!rowText(row, "Product Category").trim()) missing.push("Category");
  return missing;
}

function missingRetryFieldsForRow(row: IntakeRow) {
  if (row.Include === false) return [];
  const missing: string[] = [];
  if (!isPublicHttpsImageUrl(rowText(row, "Image URL"))) missing.push("image");
  if (!hasComplete3dDimensions(row.Dimensions)) missing.push("dimensions");
  if (!rowText(row, "Brand").trim()) missing.push("manufacturer");
  if (!rowText(row, "Product URL").trim()) missing.push("product URL");
  if (!rowText(row, "Finish / Color").trim() && !rowText(row, "Material").trim()) missing.push("finish/material");
  if (!rowText(row, "Model/SKU").trim() && !rowText(row, "SKU").trim()) missing.push("SKU/model");
  return missing;
}

function retrySummaryFromRows(beforeRows: IntakeRow[], afterRows: IntakeRow[], diagnostics?: Record<string, unknown>[]) {
  const retryMetrics = diagnostics?.find((entry) => entry.report_type === "missing_field_retry_metrics")?.summary as Record<string, unknown> | undefined;
  const beforeByIndex = beforeRows;
  let rowsImproved = 0;
  let imagesAdded = 0;
  let dimensionsAdded = 0;
  let productUrlsAdded = 0;
  let manufacturersAdded = 0;
  let skuModelsAdded = 0;
  let finishMaterialAdded = 0;
  afterRows.forEach((after, index) => {
    const before = beforeByIndex[index] || {};
    const imageAdded = !isPublicHttpsImageUrl(rowText(before, "Image URL")) && isPublicHttpsImageUrl(rowText(after, "Image URL"));
    const dimensionsAddedNow = !hasComplete3dDimensions(before.Dimensions) && hasComplete3dDimensions(after.Dimensions);
    const productUrlAdded = !rowText(before, "Product URL").trim() && Boolean(rowText(after, "Product URL").trim());
    const manufacturerAdded = !rowText(before, "Brand").trim() && Boolean(rowText(after, "Brand").trim());
    const modelAdded = !(rowText(before, "Model/SKU").trim() || rowText(before, "SKU").trim()) && Boolean(rowText(after, "Model/SKU").trim() || rowText(after, "SKU").trim());
    const finishAdded = !(rowText(before, "Finish / Color").trim() || rowText(before, "Material").trim()) && Boolean(rowText(after, "Finish / Color").trim() || rowText(after, "Material").trim());
    if (imageAdded) imagesAdded += 1;
    if (dimensionsAddedNow) dimensionsAdded += 1;
    if (productUrlAdded) productUrlsAdded += 1;
    if (manufacturerAdded) manufacturersAdded += 1;
    if (modelAdded) skuModelsAdded += 1;
    if (finishAdded) finishMaterialAdded += 1;
    if (imageAdded || dimensionsAddedNow || productUrlAdded || manufacturerAdded || modelAdded || finishAdded) rowsImproved += 1;
  });
  const fieldsStillMissing = afterRows.reduce((total, row) => total + missingRetryFieldsForRow(row).length, 0);
  return {
    rowsImproved: Number(retryMetrics?.rows_improved ?? rowsImproved),
    imagesAdded: Number(retryMetrics?.images_added ?? imagesAdded),
    dimensionsAdded: Number(retryMetrics?.dimensions_added ?? dimensionsAdded),
    productUrlsAdded: Number(retryMetrics?.product_urls_added ?? productUrlsAdded),
    manufacturersAdded: Number(retryMetrics?.manufacturers_added ?? manufacturersAdded),
    skuModelsAdded: Number(retryMetrics?.sku_model_added ?? skuModelsAdded),
    finishMaterialAdded: Number(retryMetrics?.finish_material_added ?? finishMaterialAdded),
    fieldsStillMissing: Number(retryMetrics?.fields_still_missing ?? fieldsStillMissing),
    extraCost: Number(retryMetrics?.estimated_cost_usd ?? retryMetrics?.targeted_retry_extra_cost_usd ?? 0),
  };
}

function enrichmentBudgetPreview(rows: IntakeRow[], mode: "fast" | "standard" | "deep" | "manual_retry") {
  const included = rows.filter((row) => row.Include !== false);
  const needsDimensions = included.filter((row) => rowText(row, "Brand") && rowText(row, "Model/SKU") && !hasComplete3dDimensions(row.Dimensions));
  const needsProductUrl = included.filter((row) => rowText(row, "Brand") && rowText(row, "Model/SKU") && !rowText(row, "Product URL").trim());
  const imageOnly = included.filter((row) =>
    rowText(row, "Brand") &&
    rowText(row, "Model/SKU") &&
    hasComplete3dDimensions(row.Dimensions) &&
    rowText(row, "Product URL").trim() &&
    !isPublicHttpsImageUrl(rowText(row, "Image URL")),
  );
  const caps = {
    fast: { target: 0.10, hard: 0.25, external: 12, images: 3, ai: 1, label: "Fast" },
    standard: { target: 0.25, hard: 0.50, external: 50, images: 10, ai: 6, label: "Balanced" },
    deep: { target: 1.00, hard: 2.00, external: 200, images: 50, ai: 20, label: "Deep" },
    manual_retry: { target: 1.00, hard: 2.00, external: 200, images: 50, ai: 20, label: "Manual retry" },
  }[mode];
  return {
    itemCount: included.length,
    needsDimensions: needsDimensions.length,
    needsProductUrl: needsProductUrl.length,
    imageOnly: imageOnly.length,
    ...caps,
  };
}

function isColumnMissing(row: IntakeRow, column: string) {
  if (!callFieldColumns.includes(column)) return false;
  const label = callFieldLabels[column] || column;
  return missingFieldsForRow(row).includes(label);
}

function LogoMark() {
  return (
    <div className="flex items-center gap-4">
      <div className="min-w-[132px] border-r border-linen pr-4 text-center">
        <div className="font-serif text-[28px] font-light leading-none tracking-[0.24em] text-charcoal">
          SCH
        </div>
        <div className="mt-1 text-[9px] font-medium uppercase leading-tight tracking-[0.22em] text-taupe">
          Saffron Case Homes
        </div>
      </div>
      <div>
        <div className="text-[19px] font-semibold tracking-normal text-charcoal">SCH DesignOps Intake</div>
        <div className="mt-0.5 text-xs font-medium tracking-[0.08em] text-taupe">Saffron Case Homes</div>
      </div>
    </div>
  );
}

export function IntakeWorkspace() {
  const [project, setProject] = useState("");
  const [room, setRoom] = useState("");
  const [urls, setUrls] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [bulkImages, setBulkImages] = useState<File[]>([]);
  const [bulkImageError, setBulkImageError] = useState("");
  const [isImageDragActive, setIsImageDragActive] = useState(false);
  const [photoBulkSection, setPhotoBulkSection] = useState("Decor");
  const [photoBulkCustomSection, setPhotoBulkCustomSection] = useState("");
  const [photoBulkProductName, setPhotoBulkProductName] = useState("");
  const [photoBulkAppendSequence, setPhotoBulkAppendSequence] = useState(true);
  const [photoBulkResults, setPhotoBulkResults] = useState<Record<string, {
    status: "queued" | "uploaded" | "failed";
    url?: string;
    error?: string;
    rowIndex?: number;
  }>>({});
  const [photoBulkSummary, setPhotoBulkSummary] = useState({ success: 0, failed: 0 });
  const [uploadError, setUploadError] = useState("");
  const [uploadDebug, setUploadDebug] = useState<UploadDebugInfo | null>(null);
  const [showUploadDebug, setShowUploadDebug] = useState(false);
  const [uploadStatusText, setUploadStatusText] = useState("");
  const [activePdfParseJobId, setActivePdfParseJobId] = useState("");
  const [debugUploads, setDebugUploads] = useState<DebugUploadSnapshot[]>([]);
  const [latestDiagnostics, setLatestDiagnostics] = useState<Record<string, unknown>[]>([]);
  const [debugCopyStatus, setDebugCopyStatus] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [settingsClosing, setSettingsClosing] = useState(false);
  const [preferredWebsites, setPreferredWebsites] = useState<PreferredWebsiteEntry[]>([]);
  const [preferredWebsiteForm, setPreferredWebsiteForm] = useState({ keyword: "", url: "", notes: "", id: "" });
  const [preferredWebsiteStatus, setPreferredWebsiteStatus] = useState("");
  const [preferredWebsiteBusy, setPreferredWebsiteBusy] = useState(false);
  const [websiteTheme, setWebsiteTheme] = useState<WebsiteThemeId>("light");
  const [accentColor, setAccentColor] = useState<AccentColorId>("orange");
  const [uiDensity, setUiDensity] = useState<UiDensityId>("comfortable");
  const [animationPreference, setAnimationPreference] = useState<AnimationPreferenceId>("smooth");
  const [enrichmentPriority, setEnrichmentPriority] = useState<EnrichmentPriorityId>("balanced");
  const [missingFieldRetryMode, setMissingFieldRetryMode] = useState<MissingFieldRetryModeId>("conservative");
  const [missingFieldRetryMaxRunCost, setMissingFieldRetryMaxRunCost] = useState("0.04");
  const [missingFieldRetryMaxItemCost, setMissingFieldRetryMaxItemCost] = useState("0.006");
  const [allowReplaceLowConfidenceData, setAllowReplaceLowConfidenceData] = useState(false);
  const [autoRetryFailedItems, setAutoRetryFailedItems] = useState(false);
  const [measurementUnit, setMeasurementUnit] = useState<MeasurementUnitId>("imperial");
  const [itemDebugMode, setItemDebugMode] = useState(false);
  const [imagePreview, setImagePreview] = useState<ImagePreviewState | null>(null);
  const [themeSettingsLoaded, setThemeSettingsLoaded] = useState(false);
  const [showReviewItems, setShowReviewItems] = useState(false);
  const [showMissingDetailItems, setShowMissingDetailItems] = useState(false);
  const [showEnrichedItems, setShowEnrichedItems] = useState(false);
  const [useAiPdf, setUseAiPdf] = useState(true);
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [sections, setSections] = useState<string[]>(fallbackSections);
  const [message, setMessage] = useState("");
  const [useWebEnrichment, setUseWebEnrichment] = useState(true);
  const [enrichmentMode, setEnrichmentMode] = useState<"fast" | "standard" | "deep" | "manual_retry">("fast");
  const [includeLowConfidenceImages, setIncludeLowConfidenceImages] = useState(false);
  const [photoDiscoveryReport, setPhotoDiscoveryReport] = useState<PhotoDiscoveryReport | null>(null);
  const [missingRetryStatus, setMissingRetryStatus] = useState("");
  const [missingRetrySummary, setMissingRetrySummary] = useState<ReturnType<typeof retrySummaryFromRows> | null>(null);
  const [productImageUploads, setProductImageUploads] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<"generate" | "validate" | "vendorCall" | "export" | "photoBulk" | "imageRecovery" | "missingRetry" | "">("");
  const [exportSummary, setExportSummary] = useState({
    skipped: [] as { index: number; product_name: string }[],
    missing_section: [] as { index: number; product_name: string }[],
    missing_dimensions: 0,
    missing_product_url: 0,
    missing_image_url: 0,
    image_url_present: 0,
    image_url_total: 0,
    export_count: 0,
    unique_sections: [] as string[],
    section_counts: {} as Record<string, number>,
    section_equals_product_name: [] as { index: number; product_name: string; section: string }[],
    section_too_long: [] as { index: number; product_name: string; section: string }[],
    too_many_unique_sections: false,
    canonical_sections: fallbackSections,
  });
  const [errors, setErrors] = useState<string[]>([]);
  const [vendorCall, setVendorCall] = useState<{
    row: IntakeRow;
    missingFields: string[];
    phoneNumber: string;
    customGoal: string;
    script: string;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const bulkImageInputRef = useRef<HTMLInputElement>(null);
  const settingsCloseTimeoutRef = useRef<number | null>(null);
  const pdfSessionIdRef = useRef<string>("");
  const downloadedExportFilenamesRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const storedTheme = window.localStorage.getItem(websiteThemeStorageKey);
    const storedAccent = window.localStorage.getItem(accentColorStorageKey);
    const storedDensity = window.localStorage.getItem(uiDensityStorageKey);
    const storedAnimationPreference = window.localStorage.getItem(animationPreferenceStorageKey);
    const storedEnrichmentPriority = window.localStorage.getItem(enrichmentPriorityStorageKey);
    const storedMissingFieldRetryMode = window.localStorage.getItem(missingFieldRetryModeStorageKey);
    const storedMissingFieldRetryRunCost = window.localStorage.getItem(missingFieldRetryRunCostStorageKey);
    const storedMissingFieldRetryItemCost = window.localStorage.getItem(missingFieldRetryItemCostStorageKey);
    const storedReplaceLowConfidence = window.localStorage.getItem(replaceLowConfidenceStorageKey);
    const storedAutoRetry = window.localStorage.getItem(autoRetryStorageKey);
    const storedMeasurementUnit = window.localStorage.getItem(measurementUnitStorageKey);
    const storedItemDebugMode = window.localStorage.getItem(itemDebugModeStorageKey);
    if (websiteThemeOptions.some((option) => option.id === storedTheme)) {
      setWebsiteTheme(storedTheme as WebsiteThemeId);
    }
    if (accentColorOptions.some((option) => option.id === storedAccent)) {
      setAccentColor(storedAccent as AccentColorId);
    }
    if (uiDensityOptions.some((option) => option.id === storedDensity)) {
      setUiDensity(storedDensity as UiDensityId);
    }
    if (animationPreferenceOptions.some((option) => option.id === storedAnimationPreference)) {
      setAnimationPreference(storedAnimationPreference as AnimationPreferenceId);
    }
    if (enrichmentPriorityOptions.some((option) => option.id === storedEnrichmentPriority)) {
      setEnrichmentPriority(storedEnrichmentPriority as EnrichmentPriorityId);
    }
    if (missingFieldRetryModeOptions.some((option) => option.id === storedMissingFieldRetryMode)) {
      setMissingFieldRetryMode(storedMissingFieldRetryMode as MissingFieldRetryModeId);
    }
    if (storedMissingFieldRetryRunCost && Number.isFinite(Number(storedMissingFieldRetryRunCost))) {
      setMissingFieldRetryMaxRunCost(storedMissingFieldRetryRunCost);
    }
    if (storedMissingFieldRetryItemCost && Number.isFinite(Number(storedMissingFieldRetryItemCost))) {
      setMissingFieldRetryMaxItemCost(storedMissingFieldRetryItemCost);
    }
    if (storedReplaceLowConfidence === "true" || storedReplaceLowConfidence === "false") {
      setAllowReplaceLowConfidenceData(storedReplaceLowConfidence === "true");
    }
    if (storedAutoRetry === "true" || storedAutoRetry === "false") {
      setAutoRetryFailedItems(storedAutoRetry === "true");
    }
    if (storedMeasurementUnit === "imperial" || storedMeasurementUnit === "metric") {
      setMeasurementUnit(storedMeasurementUnit);
    }
    if (storedItemDebugMode === "true" || storedItemDebugMode === "false") {
      setItemDebugMode(storedItemDebugMode === "true");
    }
    setThemeSettingsLoaded(true);
  }, []);

  useEffect(() => {
    return () => {
      if (settingsCloseTimeoutRef.current) {
        window.clearTimeout(settingsCloseTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!imagePreview) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setImagePreview(null);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [imagePreview]);

  useEffect(() => {
    if (!themeSettingsLoaded) return;
    document.documentElement.dataset.theme = websiteTheme;
    document.documentElement.dataset.accent = accentColor;
    document.documentElement.dataset.density = uiDensity;
    document.documentElement.dataset.motion = animationPreference;
    window.localStorage.setItem(websiteThemeStorageKey, websiteTheme);
    window.localStorage.setItem(accentColorStorageKey, accentColor);
    window.localStorage.setItem(uiDensityStorageKey, uiDensity);
    window.localStorage.setItem(animationPreferenceStorageKey, animationPreference);
    window.localStorage.setItem(enrichmentPriorityStorageKey, enrichmentPriority);
    window.localStorage.setItem(missingFieldRetryModeStorageKey, missingFieldRetryMode);
    window.localStorage.setItem(missingFieldRetryRunCostStorageKey, missingFieldRetryMaxRunCost);
    window.localStorage.setItem(missingFieldRetryItemCostStorageKey, missingFieldRetryMaxItemCost);
    window.localStorage.setItem(replaceLowConfidenceStorageKey, String(allowReplaceLowConfidenceData));
    window.localStorage.setItem(autoRetryStorageKey, String(autoRetryFailedItems));
    window.localStorage.setItem(measurementUnitStorageKey, measurementUnit);
    window.localStorage.setItem(itemDebugModeStorageKey, String(itemDebugMode));
  }, [accentColor, allowReplaceLowConfidenceData, animationPreference, autoRetryFailedItems, enrichmentPriority, itemDebugMode, measurementUnit, missingFieldRetryMaxItemCost, missingFieldRetryMaxRunCost, missingFieldRetryMode, themeSettingsLoaded, uiDensity, websiteTheme]);

  useEffect(() => {
    fetchHealth().catch(() => {
      setMessage("Backend is offline or not configured.");
    });
    fetchSchema()
      .then((schema) => {
        setCategories(schema.categories);
        setSections(schema.sections?.length ? schema.sections : fallbackSections);
        setPhotoBulkSection(schema.sections?.includes("Decor") ? "Decor" : schema.sections?.[0] || "General");
      })
      .catch(() => {
        setCategories([]);
        setSections(fallbackSections);
        setPhotoBulkSection("Decor");
        setMessage("Backend is offline or not configured.");
      });
    fetchPreferredWebsites()
      .then((payload) => setPreferredWebsites(payload.entries || []))
      .catch(() => {
        setPreferredWebsites([]);
      });
  }, []);

  const bulkImagePreviews = useMemo(
    () =>
      bulkImages.map((file) => ({
        file,
        url: URL.createObjectURL(file),
      })),
    [bulkImages],
  );

  useEffect(() => {
    return () => {
      bulkImagePreviews.forEach((preview) => URL.revokeObjectURL(preview.url));
    };
  }, [bulkImagePreviews]);

  const includedRows = useMemo(() => rows.filter((row) => row.Include !== false), [rows]);
  const readyRows = exportSummary.export_count;
  const missingInputRows = useMemo(
    () => includedRows.filter((row) => missingFieldsForRow(row).length > 0),
    [includedRows],
  );
  const missingRetryRows = useMemo(
    () => includedRows.filter((row) => missingRetryFieldsForRow(row).length > 0),
    [includedRows],
  );
  const productListNeedsReview = Math.max(0, includedRows.length - readyRows);
  const needsReview = useMemo(
    () => includedRows.filter((row) => row["Review Required"] === true).length,
    [includedRows],
  );
  const budgetPreview = useMemo(
    () => enrichmentBudgetPreview(rows, enrichmentMode),
    [rows, enrichmentMode],
  );
  const internalDebugReport = useMemo(
    () => buildInternalDebugReport(rows, debugUploads, errors, exportSummary, latestDiagnostics),
    [rows, debugUploads, errors, exportSummary, latestDiagnostics],
  );
  const enrichmentMetrics = internalDebugReport.enrichmentMetrics;
  const settingsUsage = useMemo(() => {
    const included = includedRows.length;
    const ready = readyRows;
    const dimensionsReady = includedRows.filter((row) => hasComplete3dDimensions(rowText(row, "Dimensions"))).length;
    const imageReady = exportSummary.image_url_present;
    const imageTotal = exportSummary.image_url_total || included;
    const topWebsites = [...preferredWebsites]
      .sort((a, b) => (Number(b.success_count || 0) - Number(a.success_count || 0)) || (Number(a.failure_count || 0) - Number(b.failure_count || 0)))
      .slice(0, 3);
    const providerCosts = enrichmentMetrics?.cost_by_provider && typeof enrichmentMetrics.cost_by_provider === "object"
      ? Object.entries(enrichmentMetrics.cost_by_provider as Record<string, unknown>)
          .map(([provider, cost]) => ({ provider, cost: Number(cost || 0) }))
          .sort((a, b) => b.cost - a.cost)
          .slice(0, 3)
      : [];

    return {
      successRate: included ? ready / included : null,
      imageSuccessRate: imageTotal ? imageReady / imageTotal : null,
      dimensionSuccessRate: included ? dimensionsReady / included : null,
      averageCostPerRun: Number(enrichmentMetrics?.estimated_cost_usd ?? 0),
      averageCostPerItem: Number(enrichmentMetrics?.avg_cost_per_item_usd ?? 0),
      topWebsites,
      providerCosts,
    };
  }, [enrichmentMetrics, exportSummary.image_url_present, exportSummary.image_url_total, includedRows, preferredWebsites, readyRows]);

  function openSettings() {
    if (settingsCloseTimeoutRef.current) {
      window.clearTimeout(settingsCloseTimeoutRef.current);
      settingsCloseTimeoutRef.current = null;
    }
    setSettingsClosing(false);
    setShowSettings(true);
  }

  function closeSettings() {
    if (settingsCloseTimeoutRef.current) {
      window.clearTimeout(settingsCloseTimeoutRef.current);
    }
    if (animationPreference === "reduced") {
      setSettingsClosing(false);
      setShowSettings(false);
      settingsCloseTimeoutRef.current = null;
      return;
    }
    setSettingsClosing(true);
    settingsCloseTimeoutRef.current = window.setTimeout(() => {
      setShowSettings(false);
      setSettingsClosing(false);
      settingsCloseTimeoutRef.current = null;
    }, 180);
  }

  const ignored = useMemo(() => rows.filter((row) => row.Include === false || row.Status === "Ignored").length, [rows]);
  const onlyPhotosSelected = bulkImages.length > 0 && files.length === 0 && !urls.trim();
  const uploadBusy = busy === "generate" || busy === "photoBulk";

  useEffect(() => {
    if (includedRows.length === 0) {
      setExportSummary({
        skipped: [],
        missing_section: [],
        missing_dimensions: 0,
        missing_product_url: 0,
        missing_image_url: 0,
        image_url_present: 0,
        image_url_total: 0,
        export_count: 0,
        unique_sections: [],
        section_counts: {},
        section_equals_product_name: [],
        section_too_long: [],
        too_many_unique_sections: false,
        canonical_sections: sections,
      });
      return;
    }
    let cancelled = false;
    validateProgramaExport(includedRows)
      .then((summary) => {
        if (!cancelled) setExportSummary(summary);
      })
      .catch(() => {
        if (!cancelled) {
          setExportSummary((current) => ({
            ...current,
            export_count: includedRows.filter((row) => rowText(row, "Product Name").trim()).length,
            skipped: includedRows
              .map((row, index) => ({ row, index }))
              .filter(({ row }) => !rowText(row, "Product Name").trim())
              .map(({ index }) => ({ index, product_name: "(no name)" })),
          }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [includedRows, sections]);

  function updateRow(index: number, key: string, value: unknown) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  }

  function resetUploadErrorState() {
    setUploadError("");
    setBulkImageError("");
    setUploadDebug(null);
    setShowUploadDebug(false);
  }

  function buildUploadDebug(filesForDebug: File[], failedStep: string, errorMessage: string, suggestedFix?: string): UploadDebugInfo {
    const firstFile = filesForDebug[0];
    return {
      fileName: uploadFileSummary(filesForDebug) || "No file selected",
      fileType: uploadFileTypeSummary(filesForDebug),
      fileSize: firstFile ? formatFileSize(filesForDebug.reduce((total, file) => total + file.size, 0)) : "0 KB",
      failedStep,
      errorMessage,
      suggestedFix: suggestedFix || suggestedUploadFix(firstFile ? classifyUploadFile(firstFile) : "unsupported", errorMessage),
    };
  }

  function setUploadFailure(filesForDebug: File[], failedStep: string, errorMessage: string, target: "pdf" | "image" = "pdf", suggestedFix?: string) {
    const debug = buildUploadDebug(filesForDebug, failedStep, errorMessage, suggestedFix);
    if (target === "image") {
      setBulkImageError(errorMessage);
    } else {
      setUploadError(errorMessage);
    }
    setUploadDebug(debug);
    setShowUploadDebug(false);
    logUploadStage("error", {
      failedStep,
      fileCount: filesForDebug.length,
      fileTypes: debug.fileType,
      error: errorMessage,
    });
  }

  async function runUploadWithTimeout<T>(
    run: (signal: AbortSignal) => Promise<T>,
    timeoutMessage: string,
  ): Promise<T> {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
    try {
      return await run(controller.signal);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new Error(timeoutMessage);
      }
      throw error;
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function pollPdfParseJob(jobId: string, file: File, allowAutoRetry = true): Promise<PdfParseJob> {
    const started = Date.now();
    let retried = false;
    let lastJob: PdfParseJob | null = null;
    while (Date.now() - started < 150_000) {
      const job = await fetchPdfParseJob(jobId);
      lastJob = job;
      setActivePdfParseJobId(job.job_id);
      const label = pdfStageLabel(job);
      setUploadStatusText(`${file.name}: ${label}`);
      if (job.stage === "ocr_fallback") {
        setMessage("OCR fallback running.");
      } else if (job.status === "queued") {
        setMessage("Queued for parsing.");
      } else if (job.status === "parsing") {
        setMessage("Parsing is taking longer than expected. Retrying with fallback parser if needed.");
      }
      if (job.status === "complete") return job;
      if (job.status === "failed" || job.status === "cancelled") {
        const logs = await fetchPdfParseLogs(job.job_id).catch(() => job);
        const rawLogs = JSON.stringify(logs, null, 2);
        if (allowAutoRetry && !retried && job.status === "failed") {
          retried = true;
          setUploadStatusText(`${file.name}: Retrying with fallback parser`);
          setMessage("Retrying with fallback parser.");
          const retry = await retryPdfParseJob(job.job_id);
          jobId = retry.job_id;
          setActivePdfParseJobId(retry.job_id);
          await sleep(500);
          continue;
        }
        setUploadDebug(pdfJobDebug(file, job, rawLogs));
        setShowUploadDebug(true);
        const parseError = new Error(job.errors?.join(" ") || `${file.name} parsing failed.`);
        Object.assign(parseError, { hasUploadDebug: true });
        throw parseError;
      }
      await sleep(1000);
    }
    if (lastJob) {
      const logs = await fetchPdfParseLogs(lastJob.job_id).catch(() => lastJob);
      setUploadDebug(pdfJobDebug(file, lastJob, JSON.stringify(logs, null, 2)));
      setShowUploadDebug(true);
    }
    const timeoutError = new Error("Parsing is taking longer than expected. The uploaded file is preserved; retry from diagnostics.");
    Object.assign(timeoutError, { hasUploadDebug: true });
    throw timeoutError;
  }

  async function parsePdfFilesWithJobs(pdfFiles: File[]): Promise<IntakeRow[]> {
    const parsedRows: IntakeRow[] = [];
    for (const [index, file] of pdfFiles.entries()) {
      const uploadedAt = new Date().toISOString();
      setUploadStatusText(`${file.name}: Uploading`);
      setMessage("Uploading PDF.");
      const upload = await runUploadWithTimeout(
        (signal) => uploadPdfForParsing({
          file,
          project,
          room,
          sessionId: pdfSessionIdRef.current || undefined,
        }, { signal }),
        "Backend waking up. Upload did not finish in time; retry once the backend is warm.",
      );
      pdfSessionIdRef.current = upload.session_id;
      setActivePdfParseJobId(upload.parse_job_id);
      setUploadStatusText(`${file.name}: Queued`);
      setMessage(`Queued ${index + 1}/${pdfFiles.length} PDF${pdfFiles.length === 1 ? "" : "s"} for parsing.`);
      try {
        const job = await pollPdfParseJob(upload.parse_job_id, file);
        const logs = await fetchPdfParseLogs(job.job_id).catch(() => null);
        setDebugUploads((current) => [...current, debugUploadSnapshot(file, job, uploadedAt, logs)]);
        parsedRows.push(...job.rows);
      } finally {
        setActivePdfParseJobId("");
      }
    }
    return parsedRows;
  }

  async function handleCancelPdfParse() {
    if (!activePdfParseJobId) return;
    try {
      await cancelPdfParseJob(activePdfParseJobId);
      setMessage("Cancellation requested.");
      setUploadStatusText("Cancelling PDF parse");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not cancel PDF parsing.");
    }
  }

  function handleFileSelection(selectedFiles: FileList | null) {
    resetUploadErrorState();
    try {
      const nextFiles = Array.from(selectedFiles ?? []);
      logUploadStage("selected", { source: "primary_file_input", fileCount: nextFiles.length });
      logUploadStage("validating", { source: "primary_file_input", fileTypes: uploadFileTypeSummary(nextFiles) });

      const pdfFiles = nextFiles.filter((file) => classifyUploadFile(file) === "pdf");
      const imageFiles = nextFiles.filter((file) => classifyUploadFile(file) === "image");
      const invalidFile = nextFiles.find((file) => classifyUploadFile(file) === "unsupported");

      if (invalidFile) {
        setFiles([]);
        setUploadFailure(
          [invalidFile],
          "validating",
          `Unsupported file type for ${invalidFile.name}. Upload a PDF or image file.`,
          "pdf",
          "Upload a PDF, JPG, PNG, or WebP file.",
        );
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }
      setFiles(pdfFiles);
      if (imageFiles.length) {
        setBulkImages(imageFiles);
        setPhotoBulkResults({});
        setPhotoBulkSummary({ success: 0, failed: 0 });
        if (bulkImageInputRef.current) bulkImageInputRef.current.value = "";
      }
      if (pdfFiles.length === 0 && imageFiles.length > 0 && fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      logUploadStage("complete", {
        source: "primary_file_input",
        pdfCount: pdfFiles.length,
        imageCount: imageFiles.length,
      });
    } catch (error) {
      setFiles([]);
      setUploadFailure(
        Array.from(selectedFiles ?? []),
        "validating",
        error instanceof Error ? error.message : "Upload failed. Please choose the file again.",
      );
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function removeFile(index: number) {
    resetUploadErrorState();
    setFiles((current) => {
      const nextFiles = current.filter((_, fileIndex) => fileIndex !== index);
      if (nextFiles.length === 0 && fileInputRef.current) fileInputRef.current.value = "";
      return nextFiles;
    });
  }

  function handleBulkImageSelection(selectedFiles: FileList | File[] | null) {
    const nextFiles = Array.from(selectedFiles ?? []);
    resetUploadErrorState();
    logUploadStage("selected", { source: "photo_file_input", fileCount: nextFiles.length });
    logUploadStage("validating", { source: "photo_file_input", fileTypes: uploadFileTypeSummary(nextFiles) });

    const imageFiles = nextFiles.filter((file) => classifyUploadFile(file) === "image");
    const pdfFiles = nextFiles.filter((file) => classifyUploadFile(file) === "pdf");
    const invalidFile = nextFiles.find((file) => classifyUploadFile(file) === "unsupported");

    if (invalidFile) {
      setUploadFailure(
        [invalidFile],
        "validating",
        `Unsupported file type for ${invalidFile.name}. Upload a PDF or image file.`,
        "image",
        "Upload a PDF, JPG, PNG, or WebP file.",
      );
      return;
    }
    if (pdfFiles.length) {
      setFiles(pdfFiles);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
    setBulkImages(imageFiles);
    setPhotoBulkResults({});
    setPhotoBulkSummary({ success: 0, failed: 0 });
    logUploadStage("complete", {
      source: "photo_file_input",
      pdfCount: pdfFiles.length,
      imageCount: imageFiles.length,
    });
  }

  function clearBulkImages() {
    setBulkImages([]);
    resetUploadErrorState();
    setPhotoBulkResults({});
    setPhotoBulkSummary({ success: 0, failed: 0 });
    if (bulkImageInputRef.current) bulkImageInputRef.current.value = "";
  }

  function createPhotoOnlyRow(file: File, index: number, secureUrl: string, status: "Needs Review" | "Missing Image") {
    const hasImage = isPublicHttpsImageUrl(secureUrl);
    const selectedSection = photoBulkSection === "__custom__" ? photoBulkCustomSection.trim() : photoBulkSection;
    return {
      Include: true,
      Project: project,
      Room: room,
      "Product Name": buildPhotoOnlyName(file.name, index, photoBulkProductName, photoBulkAppendSequence),
      Brand: "",
      Dimensions: "",
      Quantity: 1,
      Supplier: "",
      "Finish / Color": "",
      "Product Category": selectedSection,
      "Model/SKU": "",
      "Product URL": "",
      "Image URL": hasImage ? secureUrl : "",
      "Image Upload Status": hasImage ? "Uploaded" : "Missing Image",
      Notes: "Photo-only inventory item.",
      "Source Type": "Photo",
      "Import Type": "Photo-only Bulk Import",
      photo_only: true,
      Status: status,
    } satisfies IntakeRow;
  }

  async function handlePhotoBulkCreate() {
    if (!bulkImages.length) {
      setUploadFailure([], "validating", "Choose at least one image first.", "image", "Select one or more JPG, PNG, or WebP images.");
      return;
    }
    const selectedSection = photoBulkSection === "__custom__" ? photoBulkCustomSection.trim() : photoBulkSection;
    if (!selectedSection.trim()) {
      setUploadFailure(bulkImages, "validating", "Choose a section before creating rows.", "image", "Pick a section for the photo rows, then retry.");
      return;
    }
    setBusy("photoBulk");
    setUploadStatusText("Processing image...");
    setShowReviewItems(false);
    setShowMissingDetailItems(false);
    setShowEnrichedItems(false);
    resetUploadErrorState();
    setMessage("");
    logUploadStage("parsing", { source: "photo_upload", fileCount: bulkImages.length });
    const nextResults: typeof photoBulkResults = {};
    let success = 0;
    let failed = 0;
    let firstFailure: UploadDebugInfo | null = null;
    const createdRows: IntakeRow[] = [];
    const startIndex = rows.length;

    for (const [index, file] of bulkImages.entries()) {
      const key = bulkImageKey(file, index);
      nextResults[key] = { status: "queued" };
      setPhotoBulkResults({ ...nextResults });
      try {
        const response = await runUploadWithTimeout(
          (signal) => uploadImage(file, { signal }),
          "Image processing timed out. Please retry with a smaller image.",
        );
        const secureUrl = response.secure_url || "";
        if (!isPublicHttpsImageUrl(secureUrl)) throw new Error("Cloudinary did not return a public HTTPS URL.");
        const row = createPhotoOnlyRow(file, index, secureUrl, "Needs Review");
        createdRows.push(row);
        nextResults[key] = { status: "uploaded", url: secureUrl, rowIndex: startIndex + createdRows.length - 1 };
        success += 1;
      } catch (error) {
        const row = createPhotoOnlyRow(file, index, "", "Missing Image");
        createdRows.push(row);
        nextResults[key] = {
          status: "failed",
          error: error instanceof Error ? error.message : "Upload failed.",
          rowIndex: startIndex + createdRows.length - 1,
        };
        if (!firstFailure) {
          firstFailure = buildUploadDebug(
            [file],
            "parsing",
            error instanceof Error ? error.message : "Upload failed.",
            suggestedUploadFix("image", error instanceof Error ? error.message : "Upload failed."),
          );
        }
        failed += 1;
      }
      setPhotoBulkResults({ ...nextResults });
      setPhotoBulkSummary({ success, failed });
    }

    try {
      const response = await validateRows([...rows, ...createdRows]);
      setRows(response.rows);
      setErrors(response.errors);
      setMessage(`Photo-only bulk import created ${success} row${success === 1 ? "" : "s"} with images; ${failed} failed.`);
      if (firstFailure) {
        setBulkImageError(`${failed} image${failed === 1 ? "" : "s"} could not be processed.`);
        setUploadDebug(firstFailure);
      }
      logUploadStage(failed ? "error" : "complete", { source: "photo_upload", success, failed });
    } catch {
      setRows((current) => [...current, ...createdRows]);
      setMessage(`Photo-only bulk import created ${success} row${success === 1 ? "" : "s"} with images; ${failed} failed.`);
      if (firstFailure) {
        setBulkImageError(`${failed} image${failed === 1 ? "" : "s"} could not be processed.`);
        setUploadDebug(firstFailure);
      }
      logUploadStage(failed ? "error" : "complete", { source: "photo_upload", success, failed });
    } finally {
      setBusy("");
      setUploadStatusText("");
    }
  }

  async function retryPhotoUpload(file: File, index: number) {
    const key = bulkImageKey(file, index);
    const result = photoBulkResults[key];
    if (!result || result.rowIndex === undefined) return;
    setPhotoBulkResults((current) => ({ ...current, [key]: { ...result, status: "queued", error: "" } }));
    try {
      const response = await runUploadWithTimeout(
        (signal) => uploadImage(file, { signal }),
        "Image processing timed out. Please retry with a smaller image.",
      );
      const secureUrl = response.secure_url || "";
      if (!isPublicHttpsImageUrl(secureUrl)) throw new Error("Cloudinary did not return a public HTTPS URL.");
      const nextRows = rows.map((row, rowIndex) =>
        rowIndex === result.rowIndex
          ? {
              ...row,
              "Image URL": secureUrl,
              "Image Upload Status": "Uploaded",
              Status: "Needs Review",
            }
          : row,
      );
      const validated = await validateRows(nextRows);
      setRows(validated.rows);
      setErrors(validated.errors);
      setPhotoBulkResults((current) => ({
        ...current,
        [key]: { status: "uploaded", url: secureUrl, rowIndex: result.rowIndex },
      }));
      setPhotoBulkSummary((current) => ({
        success: current.success + 1,
        failed: Math.max(0, current.failed - 1),
      }));
    } catch (error) {
      setPhotoBulkResults((current) => ({
        ...current,
        [key]: {
          status: "failed",
          error: error instanceof Error ? error.message : "Upload failed.",
          rowIndex: result.rowIndex,
        },
      }));
    }
  }

  async function handleProductImageUpload(rowIndex: number, file: File | undefined) {
    if (!file) return;
    setProductImageUploads((current) => ({ ...current, [rowIndex]: "Uploading..." }));
    try {
      const response = await runUploadWithTimeout(
        (signal) => uploadImage(file, { signal }),
        "Image processing timed out. Please retry with a smaller image.",
      );
      const secureUrl = response.secure_url || "";
      if (!isPublicHttpsImageUrl(secureUrl)) throw new Error("Upload did not return a public image URL.");
      setRows((current) =>
        current.map((row, index) =>
          index === rowIndex
            ? {
                ...row,
                "Image URL": secureUrl,
                "Image Upload Status": "Uploaded",
                image_source: "manual_upload",
                confidence: "HIGH",
                evidence: "manual_upload",
                needs_image_review: "False",
              }
            : row,
        ),
      );
      setProductImageUploads((current) => ({ ...current, [rowIndex]: "" }));
    } catch (error) {
      setProductImageUploads((current) => ({
        ...current,
        [rowIndex]: error instanceof Error ? error.message : "Upload failed.",
      }));
    }
  }

  async function handleGenerate() {
    if (!urls.trim() && files.length === 0 && bulkImages.length > 0) {
      await handlePhotoBulkCreate();
      return;
    }
    if (!urls.trim() && files.length === 0) {
      setMessage("Upload images, PDFs, or paste product links first.");
      return;
    }
    setBusy("generate");
    setUploadStatusText(files.length > 0 ? "Uploading PDF..." : "Creating intake...");
    setShowReviewItems(false);
    setShowMissingDetailItems(false);
    setShowEnrichedItems(false);
    setMessage("");
    setErrors([]);
    setDebugUploads([]);
    setLatestDiagnostics([]);
    setDebugCopyStatus("");
    resetUploadErrorState();
    logUploadStage("parsing", {
      source: "intake_generate",
      pdfCount: files.length,
      imageCount: bulkImages.length,
      hasUrls: Boolean(urls.trim()),
    });
    try {
      let response: IntakeResponse;
      if (files.length > 0) {
        await fetchHealth().catch(() => {
          setMessage("Backend waking up.");
        });
        const pdfRows = await parsePdfFilesWithJobs(files);
        const urlResponse = urls.trim()
          ? await generateIntakeTable({ project, room, urls, useAiPdf, files: [] })
          : { rows: [] as IntakeRow[], errors: [] as string[] };
        response = await validateRows([...urlResponse.rows, ...pdfRows]);
        response.errors = [...(urlResponse.errors || []), ...(response.errors || [])];
      } else {
        response = await runUploadWithTimeout(
          (signal) => generateIntakeTable({ project, room, urls, useAiPdf, files }, { signal }),
          "Backend waking up. Creating intake took longer than expected.",
        );
      }
      setRows(response.rows);
      setErrors(response.errors);
      setLatestDiagnostics(response.dimension_diagnostics || []);
      setPhotoDiscoveryReport(null);
      setMessage("Intake table is ready for review.");
      logUploadStage("complete", { source: "intake_generate", rows: response.rows.length, errors: response.errors.length });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not generate the intake table.";
      setMessage(message);
      if (files.length || bulkImages.length) {
        if (typeof error === "object" && error && "hasUploadDebug" in error) {
          setUploadError(message);
          logUploadStage("error", { source: "intake_generate", error: message });
        } else {
          setUploadFailure(
            files.length ? files : bulkImages,
            "parsing",
            message,
            files.length ? "pdf" : "image",
          );
        }
      } else {
        logUploadStage("error", { source: "intake_generate", error: message });
      }
    } finally {
      setBusy("");
      setUploadStatusText("");
    }
  }

  async function handleValidate() {
    const effectiveEnrichmentMode =
      autoRetryFailedItems && INTERNAL_DEBUG_ENABLED ? "manual_retry" : enrichmentMode;
    if (["standard", "balanced", "deep", "manual_retry"].includes(effectiveEnrichmentMode) && !INTERNAL_DEBUG_ENABLED) {
      setMessage("Balanced and deep enrichment are internal/admin-only.");
      return;
    }
    if (["deep", "manual_retry"].includes(effectiveEnrichmentMode)) {
      const confirmed = window.confirm("Deep enrichment can spend materially more API budget. Continue?");
      if (!confirmed) return;
    }
    setBusy("validate");
    setShowReviewItems(false);
    setShowMissingDetailItems(false);
    setShowEnrichedItems(false);
    try {
      const response = await enrichRows({
        rows,
        useWebEnrichment,
        sessionId: pdfSessionIdRef.current || undefined,
        enrichmentMode: effectiveEnrichmentMode,
        targetedRetryMode: missingFieldRetryMode,
        maxExtraCostPerRow: optionalPositiveNumber(missingFieldRetryMaxItemCost),
        maxExtraCostPerRun: optionalPositiveNumber(missingFieldRetryMaxRunCost),
        allowReplaceLowConfidenceData: allowReplaceLowConfidenceData,
      });
      setRows(response.rows);
      setErrors(response.errors);
      setLatestDiagnostics(response.dimension_diagnostics || []);
      setPhotoDiscoveryReport(photoReportFromDiagnostics(response.dimension_diagnostics));
      const metricSummary = response.dimension_diagnostics?.find((entry) => entry.report_type === "enrichment_metrics")?.summary as Record<string, unknown> | undefined;
      const costText = metricSummary
        ? ` Enrichment cost: ${formatUsd(metricSummary.estimated_cost_usd)} · Bravi searches: ${metricSummary.bravi_searches ?? 0} · Cache hits: ${metricSummary.cache_hits ?? 0} · Avg/item: ${formatUsd(metricSummary.avg_cost_per_item_usd)}`
        : "";
      setMessage(useWebEnrichment ? `Missing info search complete.${costText}` : "Input updates saved without web search.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save input updates.");
    } finally {
      setBusy("");
    }
  }

  async function handleRetryMissingData() {
    if (!rows.length) return;
    if (!useWebEnrichment) {
      setMessage("Website enrichment is off. Enable it before trying missing data again.");
      return;
    }
    if (missingFieldRetryMode === "off") {
      setMessage("Missing-field retry is turned off in Settings.");
      return;
    }
    setBusy("missingRetry");
    setMissingRetrySummary(null);
    setMissingRetryStatus("Checking missing images...");
    const beforeRows = rows;
    try {
      await sleep(100);
      setMissingRetryStatus("Checking missing dimensions...");
      await sleep(100);
      setMissingRetryStatus("Checking cached pages and preferred websites...");
      const response = await retryMissingData({
        rows,
        useWebEnrichment,
        sessionId: pdfSessionIdRef.current || undefined,
        targetedRetryMode: missingFieldRetryMode,
        maxExtraCostPerRow: optionalPositiveNumber(missingFieldRetryMaxItemCost),
        maxExtraCostPerRun: optionalPositiveNumber(missingFieldRetryMaxRunCost),
        allowReplaceLowConfidenceData,
      });
      setMissingRetryStatus("Updating rows...");
      const summary = retrySummaryFromRows(beforeRows, response.rows, response.dimension_diagnostics);
      setRows(response.rows);
      setErrors(response.errors);
      setLatestDiagnostics(response.dimension_diagnostics || []);
      setPhotoDiscoveryReport(photoReportFromDiagnostics(response.dimension_diagnostics));
      setMissingRetrySummary(summary);
      setShowMissingDetailItems(false);
      setMessage(
        `Missing-data retry complete. ${summary.rowsImproved} rows improved · ${summary.imagesAdded} images added · ${summary.dimensionsAdded} dimensions added · ${summary.fieldsStillMissing} fields still missing · extra cost ${formatUsd(summary.extraCost)}`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not retry missing data.");
    } finally {
      setBusy("");
      setMissingRetryStatus("");
    }
  }

  async function handleRecoverImages(mode: "all" | "row", rowIndex?: number) {
    const targetRows =
      mode === "row" && typeof rowIndex === "number"
        ? rows.map((row, index) => (index === rowIndex ? { ...row, "Image URL": "" } : row))
        : rows;
    setBusy("imageRecovery");
    setMessage("");
    try {
      const response = await recoverImages(targetRows, pdfSessionIdRef.current || undefined);
      setRows(response.rows);
      setErrors(response.errors);
      setLatestDiagnostics(response.dimension_diagnostics || []);
      setPhotoDiscoveryReport(photoReportFromDiagnostics(response.dimension_diagnostics));
      setMessage("Image recovery complete.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not recover images.");
    } finally {
      setBusy("");
    }
  }

  async function handleCopyInternalDebug() {
    const text = formatDebugReportText(internalDebugReport);
    try {
      await navigator.clipboard.writeText(text);
      setDebugCopyStatus("Copied debug report.");
    } catch {
      setDebugCopyStatus("Clipboard unavailable. Download the TXT report instead.");
    }
  }

  function handleDownloadInternalDebug(format: "json" | "txt") {
    if (format === "json") {
      void downloadText("sch-intake-debug.json", JSON.stringify(internalDebugReport, null, 2));
      return;
    }
    void downloadText("sch-intake-debug.txt", formatDebugReportText(internalDebugReport));
  }

  function clearProductImage(rowIndex: number) {
    setRows((current) =>
      current.map((row, index) =>
        index === rowIndex
          ? {
              ...row,
              "Image URL": "",
              "Image Upload Status": "Missing Image",
              image_source: "",
              confidence: "",
              evidence: "",
              needs_image_review: "True",
            }
          : row,
      ),
    );
  }

  function chooseImageCandidate(rowIndex: number, candidate: Record<string, unknown>) {
    const url = candidateImageUrl(candidate);
    if (!isPublicHttpsImageUrl(url)) return;
    const confidence = String(candidate.confidence || "MEDIUM").toUpperCase();
    setRows((current) =>
      current.map((row, index) =>
        index === rowIndex
          ? {
              ...row,
              "Image URL": url,
              image_source: String(candidate.source_type || "candidate_review"),
              confidence,
              evidence: String(candidate.reason || "selected_from_candidate_review"),
              needs_image_review: confidence === "HIGH" ? "False" : "True",
              _selected_image_candidate: url,
              _image_source_type: String(candidate.source_type || "candidate_review"),
              _image_final_confidence: confidence,
            }
          : row,
      ),
    );
  }

  function downloadBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function dedupeDownloadFilename(filename: string) {
    const seen = downloadedExportFilenamesRef.current;
    const lower = filename.toLowerCase();
    if (!seen.has(lower)) {
      seen.add(lower);
      return filename;
    }
    const dot = filename.lastIndexOf(".");
    const stem = dot > 0 ? filename.slice(0, dot) : filename;
    const ext = dot > 0 ? filename.slice(dot) : "";
    let version = 2;
    while (seen.has(`${stem}_v${version}${ext}`.toLowerCase())) version += 1;
    const next = `${stem}_v${version}${ext}`;
    seen.add(next.toLowerCase());
    return next;
  }

  async function handleProgramaExport(format: "csv" | "xlsx" | "xlsx-images" | "zip") {
    setBusy("export");
    try {
      const exported =
        format === "zip"
          ? await exportProgramaZip(includedRows, includeLowConfidenceImages)
          : format === "xlsx-images"
          ? await exportProgramaXlsxWithImages(includedRows)
          : format === "xlsx"
          ? await exportProgramaXlsx(includedRows)
          : await exportProgramaCsv(includedRows);
      const filename = dedupeDownloadFilename(exported.filename);
      downloadBlob(exported.blob, filename);
      setMessage(`Use ${filename} for Programa Import Products.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not export Programa import file.");
    } finally {
      setBusy("");
    }
  }

  function openVendorCall(row: IntakeRow, missingFields: string[]) {
    setVendorCall({
      row,
      missingFields,
      phoneNumber: "",
      customGoal: buildDefaultCallGoal(row, missingFields),
      script: "",
    });
  }

  async function handleGenerateCallScript() {
    if (!vendorCall) return;
    setBusy("vendorCall");
    try {
      const response = await generateVendorCallScript({
        row: vendorCall.row,
        missingFields: vendorCall.missingFields,
        phoneNumber: vendorCall.phoneNumber,
        customGoal: vendorCall.customGoal,
      });
      setVendorCall({ ...vendorCall, script: response.script });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not generate the call script.");
    } finally {
      setBusy("");
    }
  }

  function resetPreferredWebsiteForm() {
    setPreferredWebsiteForm({ keyword: "", url: "", notes: "", id: "" });
    setPreferredWebsiteStatus("");
  }

  function editPreferredWebsite(entry: PreferredWebsiteEntry) {
    setPreferredWebsiteForm({
      keyword: entry.keyword || "",
      url: entry.url || "",
      notes: entry.notes || "",
      id: entry.id,
    });
    setPreferredWebsiteStatus("");
  }

  function preferredWebsiteUrlIsValid(value: string) {
    const raw = value.trim();
    if (!raw) return false;
    try {
      const parsed = new URL(raw.includes("://") ? raw : `https://${raw}`);
      return ["http:", "https:"].includes(parsed.protocol) && Boolean(parsed.hostname);
    } catch {
      return false;
    }
  }

  async function savePreferredWebsite() {
    const keyword = preferredWebsiteForm.keyword.trim();
    const url = preferredWebsiteForm.url.trim();
    const notes = preferredWebsiteForm.notes.trim();
    if (!keyword) {
      setPreferredWebsiteStatus("Add a brand or keyword.");
      return;
    }
    if (!preferredWebsiteUrlIsValid(url)) {
      setPreferredWebsiteStatus("Enter a valid website URL.");
      return;
    }
    setPreferredWebsiteBusy(true);
    setPreferredWebsiteStatus("");
    try {
      const response = preferredWebsiteForm.id
        ? await updatePreferredWebsite(preferredWebsiteForm.id, { keyword, url, notes })
        : await createPreferredWebsite({ keyword, url, notes });
      setPreferredWebsites(response.entries || []);
      resetPreferredWebsiteForm();
      setPreferredWebsiteStatus("Preferred website saved.");
    } catch (error) {
      setPreferredWebsiteStatus(error instanceof Error ? error.message : "Could not save preferred website.");
    } finally {
      setPreferredWebsiteBusy(false);
    }
  }

  async function removePreferredWebsite(entry: PreferredWebsiteEntry) {
    setPreferredWebsiteBusy(true);
    setPreferredWebsiteStatus("");
    try {
      const response = await deletePreferredWebsite(entry.id);
      setPreferredWebsites(response.entries || []);
      if (preferredWebsiteForm.id === entry.id) resetPreferredWebsiteForm();
      setPreferredWebsiteStatus("Preferred website deleted.");
    } catch (error) {
      setPreferredWebsiteStatus(error instanceof Error ? error.message : "Could not delete preferred website.");
    } finally {
      setPreferredWebsiteBusy(false);
    }
  }

  function exportPreferredWebsitePreferences() {
    const payload = {
      exported_at: new Date().toISOString(),
      entries: preferredWebsites.map((entry) => ({
        keyword: entry.keyword,
        url: entry.url,
        notes: entry.notes || "",
      })),
    };
    void downloadText("preferred-brand-websites.json", JSON.stringify(payload, null, 2));
    setPreferredWebsiteStatus("Website preferences exported.");
  }

  async function importPreferredWebsitePreferences(file: File | null | undefined) {
    if (!file) return;
    setPreferredWebsiteBusy(true);
    setPreferredWebsiteStatus("");
    try {
      const parsed = JSON.parse(await file.text()) as { entries?: unknown[] } | unknown[];
      const entriesToImport = Array.isArray(parsed) ? parsed : Array.isArray(parsed.entries) ? parsed.entries : [];
      if (!entriesToImport.length) {
        setPreferredWebsiteStatus("No website preferences found in that file.");
        return;
      }
      let saved = 0;
      let skipped = 0;
      for (const rawEntry of entriesToImport) {
        const entry = rawEntry as Partial<PreferredWebsiteEntry>;
        const keyword = String(entry.keyword || "").trim();
        const url = String(entry.url || "").trim();
        const notes = String(entry.notes || "").trim();
        if (!keyword || !url) {
          skipped += 1;
          continue;
        }
        try {
          await createPreferredWebsite({ keyword, url, notes });
          saved += 1;
        } catch {
          skipped += 1;
        }
      }
      const response = await fetchPreferredWebsites();
      setPreferredWebsites(response.entries || []);
      setPreferredWebsiteStatus(`Imported ${saved} website${saved === 1 ? "" : "s"}${skipped ? ` · ${skipped} skipped` : ""}.`);
    } catch {
      setPreferredWebsiteStatus("Could not import that website preferences file.");
    } finally {
      setPreferredWebsiteBusy(false);
    }
  }

  return (
    <main className="min-h-screen px-4 py-6 sm:px-7 lg:px-10">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-7">
        <header className="flex flex-col gap-4 border-b border-linen pb-5 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <LogoMark />
            <span className="rounded-full border border-orangeBorder bg-orangeSoft px-3 py-1 text-xs font-medium text-bronze">
              Internal
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge value={`${readyRows} Ready`} />
            <StatusBadge value={`${needsReview} Needs Review`} />
            <button
              type="button"
              className="btn-secondary inline-flex h-10 w-10 items-center justify-center rounded-xl"
              onClick={openSettings}
              aria-label="Open settings"
              title="Settings"
            >
              <Settings className="h-4 w-4" />
            </button>
          </div>
        </header>

        <Panel step="1" title="Upload" subtitle="Upload PDFs, links, or photos to create product entries." accent>
          <div className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Room / Location">
                <input
                  className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                  value={room}
                  onChange={(event) => setRoom(event.target.value)}
                  placeholder="Kitchen"
                />
              </Field>
              <Field label="Project Name">
                <input
                  className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                  value={project}
                  onChange={(event) => setProject(event.target.value)}
                  placeholder="Optional"
                />
              </Field>
            </div>

            <textarea
              className="input-surface min-h-28 w-full resize-none rounded-xl p-3 text-sm leading-6 text-charcoal"
              value={urls}
              onChange={(event) => setUrls(event.target.value)}
              placeholder={"Paste product links, one per line"}
            />

            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-dashed border-linen bg-paper/70 px-4 py-4 transition hover:border-orangeBorder hover:bg-orangeSoft/40">
                <span className="flex items-center gap-3">
                  <Upload className="h-5 w-5 text-bronze" />
                  <span>
                    <span className="block text-sm font-semibold text-charcoal">PDFs</span>
                    <span className="text-xs text-taupe">{files.length ? `${files.length} selected` : "Choose files"}</span>
                  </span>
                </span>
                <input
                  ref={fileInputRef}
                  className="hidden"
                  type="file"
                  accept="application/pdf,image/jpeg,image/png,image/webp,.pdf,.jpg,.jpeg,.png,.webp"
                  multiple
                  onChange={(event) => handleFileSelection(event.target.files)}
                />
              </label>

              <label
                className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-dashed px-4 py-4 transition ${
                  isImageDragActive ? "border-orangeBorder bg-orangeSoft" : "border-linen bg-paper/70 hover:border-orangeBorder hover:bg-orangeSoft/40"
                }`}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setIsImageDragActive(true);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsImageDragActive(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  setIsImageDragActive(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsImageDragActive(false);
                  handleBulkImageSelection(event.dataTransfer.files);
                }}
              >
                <span className="flex items-center gap-3">
                  <ImageIcon className="h-5 w-5 text-bronze" />
                  <span>
                    <span className="block text-sm font-semibold text-charcoal">Photos</span>
                    <span className="text-xs text-taupe">{bulkImages.length ? `${bulkImages.length} selected` : "Choose images"}</span>
                  </span>
                </span>
                <input
                  ref={bulkImageInputRef}
                  className="hidden"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                  multiple
                  onChange={(event) => handleBulkImageSelection(event.target.files)}
                />
              </label>
            </div>

            {uploadError || bulkImageError ? (
              <div className="rounded-xl border border-clay/20 bg-clay/10 px-3 py-2 text-sm text-clay">
                <div className="flex flex-wrap items-center gap-2">
                  <span>{uploadError || bulkImageError}</span>
                  {uploadDebug ? (
                    <button
                      type="button"
                      className="rounded-full border border-clay/30 px-2 py-0.5 text-xs font-semibold"
                      onClick={() => setShowUploadDebug((current) => !current)}
                    >
                      Debug
                    </button>
                  ) : null}
                </div>
                {uploadDebug && showUploadDebug ? (
                  <div className="mt-2 grid gap-1 rounded-lg border border-clay/20 bg-paper/70 p-2 text-xs text-charcoal">
                    <div><span className="font-semibold">File:</span> {uploadDebug.fileName}</div>
                    <div><span className="font-semibold">Type:</span> {uploadDebug.fileType}</div>
                    <div><span className="font-semibold">Size:</span> {uploadDebug.fileSize}</div>
                    <div><span className="font-semibold">Failed step:</span> {uploadDebug.failedStep}</div>
                    <div><span className="font-semibold">Error:</span> {uploadDebug.errorMessage}</div>
                    {uploadDebug.parserAttempted ? <div><span className="font-semibold">Parser attempted:</span> {uploadDebug.parserAttempted}</div> : null}
                    {uploadDebug.timeoutReason ? <div><span className="font-semibold">Timeout reason:</span> {uploadDebug.timeoutReason}</div> : null}
                    {uploadDebug.stackTraceSnippet ? (
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-charcoal/5 p-2">{uploadDebug.stackTraceSnippet}</pre>
                    ) : null}
                    <div><span className="font-semibold">Suggested fix:</span> {uploadDebug.suggestedFix}</div>
                    <div className="flex flex-wrap gap-2 pt-1">
                      {uploadDebug.retryJobId ? (
                        <button
                          type="button"
                          className="rounded-full border border-clay/30 px-2 py-0.5 text-xs font-semibold"
                          onClick={async () => {
                            try {
                              setBusy("generate");
                              setUploadStatusText("Retrying parser");
                              const retry = await retryPdfParseJob(uploadDebug.retryJobId || "");
                              const file = files[0];
                              if (!file) return;
                              const job = await pollPdfParseJob(retry.job_id, file, false);
                              const validated = await validateRows(job.rows);
                              setRows(validated.rows);
                              setErrors(validated.errors);
                              setUploadError("");
                              setShowUploadDebug(false);
                              setMessage("PDF parsing retry complete.");
                            } catch (error) {
                              setUploadError(error instanceof Error ? error.message : "Retry failed.");
                            } finally {
                              setBusy("");
                              setUploadStatusText("");
                            }
                          }}
                        >
                          Retry parsing
                        </button>
                      ) : null}
                      {uploadDebug.rawLogs ? (
                        <button
                          type="button"
                          className="rounded-full border border-clay/30 px-2 py-0.5 text-xs font-semibold"
                          onClick={() => downloadText("pdf-parse-logs.json", uploadDebug.rawLogs || "")}
                        >
                          Download raw logs
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {files.length > 0 ? (
              <div className="flex flex-wrap gap-2 text-xs text-taupe">
                {files.map((file, index) => (
                  <button
                    key={`${file.name}-${file.size}-${file.lastModified}`}
                    type="button"
                    className="rounded-full border border-linen bg-paper px-3 py-1 hover:border-orangeBorder"
                    onClick={() => removeFile(index)}
                    title={`Remove ${file.name}`}
                  >
                    {file.name}
                  </button>
                ))}
              </div>
            ) : null}

            {bulkImages.length > 0 ? (
              <div className="grid gap-3 border-t border-linen pt-4">
                <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                  <Field label="Section">
                    <select
                      className="input-surface h-11 rounded-xl px-3 text-sm text-charcoal"
                      value={photoBulkSection}
                      onChange={(event) => setPhotoBulkSection(event.target.value)}
                    >
                      {sections.map((section) => (
                        <option key={section} value={section}>
                          {section}
                        </option>
                      ))}
                      <option value="__custom__">Custom</option>
                    </select>
                  </Field>
                  <Field label="Product Name">
                    <input
                      className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                      value={photoBulkProductName}
                      onChange={(event) => setPhotoBulkProductName(event.target.value)}
                      placeholder="Optional"
                    />
                  </Field>
                  <label className="flex h-11 items-center gap-2 text-sm text-taupe">
                    <input
                      type="checkbox"
                      checked={photoBulkAppendSequence}
                      onChange={(event) => setPhotoBulkAppendSequence(event.target.checked)}
                      className="h-4 w-4 accent-bronze"
                    />
                    Number names
                  </label>
                </div>
                {photoBulkSection === "__custom__" ? (
                  <input
                    className="input-surface h-11 rounded-xl px-3 text-sm text-charcoal"
                    value={photoBulkCustomSection}
                    onChange={(event) => setPhotoBulkCustomSection(event.target.value)}
                    placeholder="Custom section"
                  />
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <StatusBadge value={`${photoBulkSummary.success} uploaded`} />
                  <StatusBadge value={`${photoBulkSummary.failed} failed`} />
                  {bulkImages.length > 0 ? (
                    <button type="button" className="text-xs font-semibold text-taupe underline-offset-2 hover:underline" onClick={clearBulkImages}>
                      Clear photos
                    </button>
                  ) : null}
                </div>
                <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                  {bulkImagePreviews.slice(0, 6).map(({ file, url }, index) => (
                    <img key={`${file.name}-${file.size}-${file.lastModified}`} src={url} alt={file.name} className="h-20 w-full rounded-xl object-cover" />
                  ))}
                </div>
                <button
                  type="button"
                  className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:cursor-not-allowed disabled:text-taupe/60"
                  disabled={busy === "photoBulk" || !bulkImages.length}
                  onClick={handlePhotoBulkCreate}
                >
                  {busy === "photoBulk" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImageIcon className="h-4 w-4" />}
                  {busy === "photoBulk" ? uploadStatusText || "Processing image..." : "Upload Photos"}
                </button>
              </div>
            ) : null}

            <label className="flex items-center gap-2 text-sm text-taupe">
              <input
                type="checkbox"
                checked={useAiPdf}
                onChange={(event) => setUseAiPdf(event.target.checked)}
                className="h-4 w-4 accent-bronze"
              />
              Use AI for PDFs
            </label>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <button
                className="btn-primary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-6 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
                disabled={uploadBusy}
                onClick={handleGenerate}
              >
                {uploadBusy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : onlyPhotosSelected ? (
                  <ImageIcon className="h-4 w-4" />
                ) : (
                  <FileText className="h-4 w-4" />
                )}
                {uploadBusy ? uploadStatusText || (busy === "photoBulk" ? "Processing image..." : "Reading PDF...") : onlyPhotosSelected ? "Upload Photos" : "Upload"}
              </button>
              {busy === "generate" && activePdfParseJobId ? (
                <button
                  type="button"
                  className="inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-clay/30 px-4 text-sm font-semibold text-clay hover:bg-clay/10"
                  onClick={handleCancelPdfParse}
                >
                  <X className="h-4 w-4" />
                  Cancel parse
                </button>
              ) : null}
              {message ? <p className="text-sm text-charcoal/65">{message}</p> : null}
            </div>
          </div>
        </Panel>

        {errors.length ? (
          <div className="rounded-xl border border-clay/20 bg-clay/10 px-4 py-3 text-sm text-clay">
            {errors.map((error) => (
              <div key={error}>{error}</div>
            ))}
          </div>
        ) : null}

        <Panel step="2" title="Review" subtitle="Check and edit product entries." accent>
          {rows.length > 0 ? (
            <>
              <div className="mb-3 flex flex-wrap gap-2">
                <StatusBadge value={`${readyRows} Ready`} />
                <StatusBadge value={`${missingInputRows.length} Needs Review`} />
              </div>
              <ProductListDisclosure
                expanded={showReviewItems}
                onToggle={() => setShowReviewItems((current) => !current)}
                collapsedLabel={`Review product items (${includedRows.length})`}
                expandedLabel="Hide product items"
                total={includedRows.length}
                ready={readyRows}
                needsReview={productListNeedsReview}
              >
                <div className="overflow-x-auto rounded-xl border border-linen bg-paper">
                  <table className="min-w-[1180px] w-full border-separate border-spacing-0 text-left text-sm">
                    <thead>
                      <tr>
                        {reviewColumns.map((column) => (
                          <th key={column} className="sticky top-0 border-b border-linen bg-ivory px-3 py-3 text-xs font-semibold text-charcoal/60">
                            {column === "Room"
                              ? "Location"
                              : column === "Quantity"
                                ? "Qty"
                                : column === "Supplier"
                                  ? "Supplier / Who Bought From"
                                  : column === "Finish / Color"
                                    ? "Finish"
                                    : column === "Product Category"
                                      ? "Category"
                                      : column === "Confidence Score"
                                        ? "Confidence"
                                        : column === "Review Required"
                                          ? "Needs Review"
                                          : column === "Status"
                                            ? "Status / Needs Review"
                                            : column}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, index) => (
                        <tr key={index} className="align-top transition-colors hover:bg-orangeSoft/30">
                          {reviewColumns.map((column) => (
                            <td key={column} className="border-b border-linen/70 px-3 py-3">
                              <Cell
                                row={row}
                                column={column}
                                categories={categories}
                                sections={sections}
                                onChange={(value) => updateRow(index, column, value)}
                                onVendorCall={(fields) => openVendorCall(row, fields)}
                              />
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </ProductListDisclosure>
            </>
          ) : (
            <div className="rounded-xl border border-dashed border-linen bg-paper/60 px-5 py-10 text-center">
              <p className="text-sm text-taupe">Uploaded products will appear here.</p>
            </div>
          )}
        </Panel>

        <Panel step="3" title="Enrich" subtitle="Fill missing product details automatically." accent>
          <div className="grid gap-4">
            <label className="flex items-start gap-3 text-sm text-taupe">
              <input
                type="checkbox"
                checked={useWebEnrichment}
                onChange={(event) => setUseWebEnrichment(event.target.checked)}
                className="mt-1 h-4 w-4 accent-bronze"
              />
              Search websites for missing details
            </label>

            <Field label="Search depth">
              <select
                className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                value={enrichmentMode}
                onChange={(event) => setEnrichmentMode(event.target.value as typeof enrichmentMode)}
                disabled={!useWebEnrichment}
              >
                <option value="fast">Fast</option>
                {INTERNAL_DEBUG_ENABLED ? <option value="standard">Balanced</option> : null}
                {INTERNAL_DEBUG_ENABLED ? <option value="deep">Deep</option> : null}
                {INTERNAL_DEBUG_ENABLED ? <option value="manual_retry">Retry failed items</option> : null}
              </select>
            </Field>

            {useWebEnrichment ? (
              <div className="rounded-xl border border-orange/25 bg-orangeSoft/30 px-4 py-3 text-sm text-charcoal">
                <div className="font-semibold">{budgetPreview.label} search is ready.</div>
                <div className="mt-1 text-xs text-taupe">
                  {budgetPreview.itemCount} items · {budgetPreview.needsDimensions} need dimensions · {budgetPreview.imageOnly} need images.
                </div>
                {busy === "validate" ? (
                  <div className="mt-2 rounded-lg border border-orange/20 bg-paper/50 px-3 py-2 text-xs text-bronze">
                    Searching trusted sources and filling missing details.
                  </div>
                ) : null}
              </div>
            ) : null}

            {enrichmentMetrics ? (
              <div className="rounded-xl border border-sage/20 bg-sage/10 px-4 py-3 text-sm text-charcoal">
                <div className="font-semibold">
                  Last run complete · Average cost per item {formatUsd(enrichmentMetrics.avg_cost_per_item_usd)}
                </div>
                <div className="mt-1 text-xs text-taupe">
                  Detailed cost, cache, and provider information is available in Settings → Advanced.
                </div>
              </div>
            ) : null}

            {rows.length > 0 && missingInputRows.length ? (
              <ProductListDisclosure
                expanded={showMissingDetailItems}
                onToggle={() => setShowMissingDetailItems((current) => !current)}
                collapsedLabel={`Review missing details (${missingInputRows.length})`}
                expandedLabel="Hide missing details"
                total={includedRows.length}
                ready={readyRows}
                needsReview={productListNeedsReview}
              >
                <div className="divide-y divide-linen rounded-xl border border-linen bg-paper">
                  {rows.map((row, index) => {
                    const missingFields = row.Include !== false ? missingFieldsForRow(row) : [];
                    return missingFields.length > 0 ? (
                      <div key={index} className="grid gap-3 p-4 lg:grid-cols-[0.8fr_1.2fr] lg:items-start">
                        <div>
                          <div className="text-sm font-semibold text-charcoal">{rowText(row, "Product Name") || "Unnamed Item"}</div>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {missingFields.map((field) => (
                              <StatusBadge
                                key={field}
                                value={field === "Dimensions" ? "Missing Dimensions" : field === "Image URL" ? "Missing Image" : `Missing ${field}`}
                              />
                            ))}
                          </div>
                        </div>
                        <div className="grid gap-2">
                          {missingFields.map((field) => {
                            const key = missingFieldKeys[field];
                            return (
                              <MissingInputField
                                key={field}
                                field={field}
                                value={rowText(row, key)}
                                onChange={(value) => updateRow(index, key, key === "Quantity" && value ? Number(value) : value)}
                                onVendorCall={() => openVendorCall(row, [field])}
                              />
                            );
                          })}
                        </div>
                      </div>
                    ) : null;
                  })}
                </div>
              </ProductListDisclosure>
            ) : rows.length > 0 ? (
              <div className="flex items-center gap-2 rounded-xl border border-sage/20 bg-sage/10 p-4 text-sm text-sage">
                <CheckCircle2 className="h-4 w-4" />
                No missing details.
              </div>
            ) : (
              <p className="text-sm text-taupe">Create product entries first.</p>
            )}
            <button
              className="btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
              disabled={busy === "validate" || busy === "missingRetry" || rows.length === 0}
              onClick={handleValidate}
            >
              {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              Fill Missing Details
            </button>
            {enrichmentMetrics && missingRetryRows.length > 0 ? (
              <div className="grid gap-3 rounded-xl border border-orange/20 bg-paper/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-charcoal">Still missing data?</div>
                    <div className="mt-1 text-xs text-taupe">
                      {missingRetryRows.length} rows have missing images, dimensions, URLs, manufacturers, finish/material, or model data.
                    </div>
                  </div>
                  <button
                    className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={busy !== "" || missingFieldRetryMode === "off" || !useWebEnrichment}
                    onClick={handleRetryMissingData}
                  >
                    {busy === "missingRetry" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    Try Again for Missing Data
                  </button>
                </div>
                {busy === "missingRetry" ? (
                  <div className="rounded-lg border border-orange/20 bg-orangeSoft/25 px-3 py-2 text-xs text-bronze">
                    {missingRetryStatus || "Checking missing data..."}
                  </div>
                ) : null}
                {missingRetrySummary ? (
                  <div className="grid gap-2 text-xs text-taupe sm:grid-cols-5">
                    <div><span className="block font-semibold text-charcoal">{missingRetrySummary.rowsImproved}</span>Rows improved</div>
                    <div><span className="block font-semibold text-charcoal">{missingRetrySummary.imagesAdded}</span>Images added</div>
                    <div><span className="block font-semibold text-charcoal">{missingRetrySummary.dimensionsAdded}</span>Dimensions added</div>
                    <div><span className="block font-semibold text-charcoal">{missingRetrySummary.fieldsStillMissing}</span>Fields still missing</div>
                    <div><span className="block font-semibold text-charcoal">{formatUsd(missingRetrySummary.extraCost)}</span>Extra cost</div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel step="4" title="Export" subtitle="Download product data and an image package for Programa.">
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-2">
              <StatusBadge value={`${exportSummary.export_count} Export Ready`} />
              <StatusBadge value={`Image refs ${exportSummary.image_url_present}/${exportSummary.image_url_total}`} />
              <StatusBadge value={`${exportSummary.missing_section.length} Missing Section`} />
            </div>
            <div className="rounded-xl border border-bronze/20 bg-bronze/10 p-3 text-sm text-charcoal">
              Programa&apos;s importer expects images on the same spreadsheet row as the product. Use Excel with Images for image import, or ZIP for matched manual upload.
            </div>
            <div className="grid gap-3 sm:grid-cols-4">
              <button
                className="btn-primary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                disabled={busy === "export" || exportSummary.export_count === 0}
                onClick={() => handleProgramaExport("xlsx-images")}
              >
                {busy === "export" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Excel with Images
              </button>
              <button
                className="btn-secondary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                disabled={busy === "export" || exportSummary.export_count === 0}
                onClick={() => handleProgramaExport("zip")}
              >
                <Download className="h-4 w-4" />
                Download ZIP
              </button>
              <button
                className="btn-secondary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                disabled={busy === "export" || exportSummary.export_count === 0}
                onClick={() => handleProgramaExport("csv")}
              >
                <Download className="h-4 w-4" />
                Download CSV
              </button>
              <button
                className="btn-secondary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                disabled={busy === "export" || exportSummary.export_count === 0}
                onClick={() => handleProgramaExport("xlsx")}
              >
                <Download className="h-4 w-4" />
                Download XLSX
              </button>
            </div>
            <label className="flex items-center gap-2 text-xs text-taupe">
              <input
                type="checkbox"
                checked={includeLowConfidenceImages}
                onChange={(event) => setIncludeLowConfidenceImages(event.target.checked)}
                className="h-4 w-4 accent-bronze"
              />
              Include low-confidence images in ZIP
            </label>
            <div className="border-t border-linen pt-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-base font-semibold text-charcoal">Review &amp; Complete Product Data</h3>
                <button
                  className="btn-secondary inline-flex h-9 items-center justify-center gap-2 rounded-xl px-3 text-xs font-semibold disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                  disabled={busy === "imageRecovery" || !rows.length}
                  onClick={() => handleRecoverImages("all")}
                >
                  {busy === "imageRecovery" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  Re-run Missing Images
                </button>
              </div>
              {photoDiscoveryReport ? (
                <div className="mt-3 rounded-xl border border-linen bg-ivory/50 p-3">
                  <div className="grid gap-2 text-xs text-taupe sm:grid-cols-3 lg:grid-cols-6">
                    <div><span className="block font-semibold text-charcoal">{photoDiscoveryReport.total_rows}</span>Rows</div>
                    <div><span className="block font-semibold text-charcoal">{photoDiscoveryReport.official_product_pages_found}</span>Official pages</div>
                    <div><span className="block font-semibold text-charcoal">{photoDiscoveryReport.images_found}</span>Images found</div>
                    <div><span className="block font-semibold text-charcoal">{photoDiscoveryReport.images_inserted_into_excel}</span>Excel images</div>
                    <div><span className="block font-semibold text-charcoal">{photoDiscoveryReport.rows_needing_review}</span>Needs review</div>
                    <div><span className="block font-semibold text-charcoal">{photoDiscoveryReport.rows_missing_images}</span>Missing images</div>
                  </div>
                  {photoDiscoveryReport.failed_rows.length ? (
                    <div className="mt-3 space-y-2">
                      {photoDiscoveryReport.failed_rows.slice(0, 3).map((failed, failedIndex) => (
                        <div key={`${failed.model_sku}-${failedIndex}`} className="rounded-lg border border-linen bg-paper p-2 text-xs text-taupe">
                          <div className="font-semibold text-charcoal">
                            {failed.product_name || "Unnamed product"} {failed.brand || failed.model_sku ? <span className="font-normal text-taupe">· {[failed.brand, failed.model_sku].filter(Boolean).join(" ")}</span> : null}
                          </div>
                          <div className="mt-1">{failed.why_it_failed || "Needs review"}</div>
                          <div className="mt-1 text-charcoal">{failed.recommended_next_action}</div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {includedRows.length ? (
                <div className="mt-3">
                  <ProductListDisclosure
                    expanded={showEnrichedItems}
                    onToggle={() => setShowEnrichedItems((current) => !current)}
                    collapsedLabel={`Review enriched items (${includedRows.length})`}
                    expandedLabel="Hide product items"
                    total={includedRows.length}
                    ready={readyRows}
                    needsReview={productListNeedsReview}
                  >
                    <div className="divide-y divide-linen rounded-xl border border-linen bg-paper">
                      {rows.map((row, index) => {
                        if (row.Include === false) return null;
                        const productName = rowText(row, "Product Name");
                        const brand = rowText(row, "Brand");
                        const dimensions = rowText(row, "Dimensions");
                        const productUrl = rowText(row, "Product URL");
                        const imageUrl = rowText(row, "Image URL");
                        const imageSource = rowText(row, "image_source");
                        const confidence = rowText(row, "confidence");
                        const evidence = rowText(row, "evidence");
                        const needsReview = rowText(row, "needs_image_review").toLowerCase() === "true";
                        const uploadStatus = productImageUploads[index] || "";
                        const rawGroupedText = rowText(row, "_raw_grouped_text");
                        const parsedFields = rowText(row, "_parsed_fields");
                        const enrichmentQuery = rowText(row, "_enrichment_query_used");
                        const confidenceReason = rowText(row, "_confidence_reason");
                        const missingInitial = rowText(row, "_missing_fields_initial");
                        const imageQueryUsed = rowText(row, "_image_query_used");
                        const imageCandidates = safeParseArray(rowText(row, "_image_candidates"))
                          .filter((candidate) => isPublicHttpsImageUrl(candidateImageUrl(candidate)))
                          .slice(0, 3);
                        const imageRejectedCandidates = rowText(row, "_image_rejected_candidates");
                        const callFields = phoneCallFieldsForRow(row);
                        const emphasizeCall = shouldEmphasizePhoneCall(row);
                        const imagePreviewState = imagePreviewStateForRow(row);
                        const itemDebugAvailable = itemDebugMode;
                        return (
                          <div key={index} className="grid gap-3 p-4 md:grid-cols-[1fr_1.2fr_auto] md:items-center">
                            <div className="min-w-0">
                              <div className={productName ? "truncate text-sm font-semibold text-charcoal" : "text-sm font-semibold text-clay"}>
                                {productName || "Missing"}
                              </div>
                              <div className="mt-1 text-xs text-taupe">Brand: <ReviewValue value={brand} /></div>
                            </div>
                            <div className="grid gap-1 text-xs text-taupe sm:grid-cols-2">
                              <div>Dimensions: <ReviewValue value={dimensions} /></div>
                              <div>Product URL: <ReviewValue value={productUrl} /></div>
                              <div>Source: <ReviewValue value={imageSource} /></div>
                              <div>
                                Confidence:{" "}
                                {confidence ? <StatusBadge value={`${confidence}${needsReview ? " Review" : ""}`} /> : <span className="font-semibold text-clay">Missing</span>}
                              </div>
                              <div className="sm:col-span-2">
                                Image:{" "}
                                {imageUrl ? (
                                  <button
                                    type="button"
                                    className="inline-flex max-w-full items-center gap-2 rounded-lg align-middle text-left hover:bg-orangeSoft/35"
                                    onClick={() => imagePreviewState ? setImagePreview(imagePreviewState) : null}
                                  >
                                    <span className="relative h-8 w-8 shrink-0 overflow-hidden rounded-lg">
                                      <img src={imageUrl} alt={productName || "Product image"} className="h-8 w-8 object-cover" />
                                      <span className="absolute inset-0 grid place-items-center bg-black/0 text-white opacity-0 transition hover:bg-black/25 hover:opacity-100">
                                        <Maximize2 className="h-3.5 w-3.5" />
                                      </span>
                                    </span>
                                    <span className="truncate text-charcoal">{imageUrl}</span>
                                  </button>
                                ) : (
                                  <span className="font-semibold text-clay">Missing</span>
                                )}
                              </div>
                              {uploadStatus ? (
                                <div className={`sm:col-span-2 ${uploadStatus === "Uploading..." ? "text-bronze" : "text-clay"}`}>
                                  {uploadStatus}
                                </div>
                              ) : null}
                              {itemDebugMode && evidence ? <div className="sm:col-span-2 truncate">Evidence: {evidence}</div> : null}
                              {imageCandidates.length ? (
                                <div className="sm:col-span-2 mt-1 flex flex-wrap gap-2">
                                  {imageCandidates.map((candidate, candidateIndex) => {
                                    const candidateUrl = candidateImageUrl(candidate);
                                    return (
                                      <button
                                        key={`${candidateUrl}-${candidateIndex}`}
                                        type="button"
                                        className="inline-flex max-w-full items-center gap-2 rounded-lg border border-orangeBorder/60 bg-orangeSoft/40 px-2 py-1 text-left text-xs text-charcoal hover:bg-orangeSoft"
                                        onClick={() => chooseImageCandidate(index, candidate)}
                                      >
                                        <img src={candidateUrl} alt="" className="h-7 w-7 rounded-md object-cover" />
                                        <span className="max-w-[220px] truncate">{String(candidate.source_type || candidate.confidence || "Candidate")}</span>
                                      </button>
                                    );
                                  })}
                                </div>
                              ) : null}
                              {itemDebugAvailable ? (
                                <details className="sm:col-span-2 rounded-lg border border-linen bg-ivory/40 p-2">
                                  <summary className="cursor-pointer text-xs font-semibold text-charcoal">Item debug metadata</summary>
                                  <div className="mt-2 grid gap-2 text-xs text-taupe">
                                    <div className="grid gap-1 rounded-md border border-linen bg-paper/70 p-2 sm:grid-cols-2">
                                      {itemDebugRows(row).map(([label, value]) => (
                                        <div key={label} className="min-w-0">
                                          <span className="font-semibold text-charcoal">{label}:</span>{" "}
                                          <span className="break-words">{String(value || "none")}</span>
                                        </div>
                                      ))}
                                    </div>
                                    {rawGroupedText ? (
                                      <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-paper p-2 text-charcoal">{rawGroupedText}</pre>
                                    ) : null}
                                    {parsedFields ? <div><span className="font-semibold text-charcoal">Parsed:</span> {parsedFields}</div> : null}
                                    {enrichmentQuery ? <div><span className="font-semibold text-charcoal">Query:</span> {enrichmentQuery}</div> : null}
                                    {imageQueryUsed ? <div><span className="font-semibold text-charcoal">Image query:</span> {imageQueryUsed}</div> : null}
                                    {imageCandidates.length ? <div><span className="font-semibold text-charcoal">Image candidates:</span> {imageCandidates.map((candidate) => candidateImageUrl(candidate)).join(" | ")}</div> : null}
                                    {imageRejectedCandidates ? <div><span className="font-semibold text-charcoal">Rejected images:</span> {imageRejectedCandidates}</div> : null}
                                    {missingInitial ? <div><span className="font-semibold text-charcoal">Missing:</span> {missingInitial}</div> : null}
                                    {confidenceReason ? <div><span className="font-semibold text-charcoal">Reason:</span> {confidenceReason}</div> : null}
                                  </div>
                                </details>
                              ) : null}
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <label className="btn-secondary inline-flex h-10 cursor-pointer items-center justify-center rounded-xl px-4 text-sm font-semibold">
                                {imageUrl ? "Replace" : "Upload Image"}
                                <input
                                  className="hidden"
                                  type="file"
                                  accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                                  onChange={(event) => {
                                    void handleProductImageUpload(index, event.target.files?.[0]);
                                    event.currentTarget.value = "";
                                  }}
                                />
                              </label>
                              <button
                                type="button"
                                className={`btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-3 text-sm font-semibold ${
                                  emphasizeCall ? "border-orangeBorder bg-orangeSoft/50 text-bronze hover:bg-orangeSoft" : ""
                                }`}
                                onClick={() => openVendorCall(row, callFields.length ? callFields : ["Spec confirmation"])}
                                title="Call supplier/manufacturer to confirm specs or request product assets"
                                aria-label="Phone call"
                              >
                                <Phone className="h-4 w-4" />
                                <span>Phone Call</span>
                              </button>
                              {imageUrl ? (
                                <button
                                  className="btn-secondary inline-flex h-10 items-center justify-center rounded-xl px-3 text-sm font-semibold"
                                  onClick={() => clearProductImage(index)}
                                  aria-label="Clear image"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              ) : null}
                              <button
                                className="btn-secondary inline-flex h-10 items-center justify-center rounded-xl px-3 text-sm font-semibold disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                                disabled={busy === "imageRecovery"}
                                onClick={() => handleRecoverImages("row", index)}
                                aria-label="Re-run image recovery"
                              >
                                {busy === "imageRecovery" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </ProductListDisclosure>
                </div>
              ) : (
                <p className="mt-2 text-sm text-taupe">Create product entries first.</p>
              )}
              {includedRows.length ? (
                <button
                  className="btn-primary mt-4 inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                  disabled={busy === "export" || exportSummary.export_count === 0}
                  onClick={() => handleProgramaExport("csv")}
                >
                  {busy === "export" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                  Download Updated CSV
                </button>
              ) : null}
            </div>
          </div>
        </Panel>
        {showSettings ? (
          <SettingsDialog
            entries={preferredWebsites}
            form={preferredWebsiteForm}
            status={preferredWebsiteStatus}
            busy={preferredWebsiteBusy}
            websiteTheme={websiteTheme}
            accentColor={accentColor}
            uiDensity={uiDensity}
            animationPreference={animationPreference}
            useWebEnrichment={useWebEnrichment}
            enrichmentMode={enrichmentMode}
            enrichmentPriority={enrichmentPriority}
            missingFieldRetryMode={missingFieldRetryMode}
            missingFieldRetryMaxRunCost={missingFieldRetryMaxRunCost}
            missingFieldRetryMaxItemCost={missingFieldRetryMaxItemCost}
            allowReplaceLowConfidenceData={allowReplaceLowConfidenceData}
            autoRetryFailedItems={autoRetryFailedItems}
            measurementUnit={measurementUnit}
            itemDebugMode={itemDebugMode}
            includeLowConfidenceImages={includeLowConfidenceImages}
            budgetPreview={budgetPreview}
            enrichmentMetrics={enrichmentMetrics}
            usage={settingsUsage}
            debugCopyStatus={debugCopyStatus}
            isClosing={settingsClosing}
            onClose={closeSettings}
            onChangeTheme={setWebsiteTheme}
            onChangeAccent={setAccentColor}
            onChangeDensity={setUiDensity}
            onChangeAnimationPreference={setAnimationPreference}
            onChangeUseWebEnrichment={setUseWebEnrichment}
            onChangeEnrichmentMode={setEnrichmentMode}
            onChangeEnrichmentPriority={setEnrichmentPriority}
            onChangeMissingFieldRetryMode={setMissingFieldRetryMode}
            onChangeMissingFieldRetryMaxRunCost={setMissingFieldRetryMaxRunCost}
            onChangeMissingFieldRetryMaxItemCost={setMissingFieldRetryMaxItemCost}
            onChangeAllowReplaceLowConfidenceData={setAllowReplaceLowConfidenceData}
            onChangeAutoRetry={setAutoRetryFailedItems}
            onChangeMeasurementUnit={setMeasurementUnit}
            onChangeItemDebugMode={setItemDebugMode}
            onChangeIncludeLowConfidenceImages={setIncludeLowConfidenceImages}
            onChangeForm={setPreferredWebsiteForm}
            onSave={savePreferredWebsite}
            onEdit={editPreferredWebsite}
            onDelete={removePreferredWebsite}
            onReset={resetPreferredWebsiteForm}
            onImportWebsites={importPreferredWebsitePreferences}
            onExportWebsites={exportPreferredWebsitePreferences}
            onCopyDebug={handleCopyInternalDebug}
            onDownloadDebug={handleDownloadInternalDebug}
          />
        ) : null}
        {vendorCall ? (
          <VendorCallDialog
            state={vendorCall}
            busy={busy === "vendorCall"}
            onChange={setVendorCall}
            onClose={() => setVendorCall(null)}
            onGenerateScript={handleGenerateCallScript}
          />
        ) : null}
        {imagePreview ? (
          <ProductImageLightbox
            preview={imagePreview}
            onClose={() => setImagePreview(null)}
          />
        ) : null}
      </div>
    </main>
  );
}

function StatusBadge({ value }: { value: string }) {
  const normal = value.toLowerCase();
  const tone =
    normal.startsWith("0 failed")
      ? "border-linen bg-paper/70 text-taupe"
      : normal.includes("ready") || normal.includes("uploaded")
      ? "border-sage/20 bg-sage/10 text-sage"
      : normal.includes("missing") || normal.includes("failed")
        ? "border-clay/20 bg-clay/10 text-clay"
        : normal.includes("review")
          ? "border-orangeBorder bg-orangeSoft text-bronze"
          : "border-linen bg-paper/70 text-taupe";
  return (
    <span className={`inline-flex min-h-6 items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${tone}`}>
      {value}
    </span>
  );
}

function ReviewValue({ value }: { value: string }) {
  return value ? <span className="text-charcoal">{value}</span> : <span className="font-semibold text-clay">Missing</span>;
}

function ProductListDisclosure({
  expanded,
  onToggle,
  collapsedLabel,
  expandedLabel,
  total,
  ready,
  needsReview,
  children,
}: {
  expanded: boolean;
  onToggle: () => void;
  collapsedLabel: string;
  expandedLabel: string;
  total: number;
  ready: number;
  needsReview: number;
  children: ReactNode;
}) {
  const summary = `${total} item${total === 1 ? "" : "s"} found · ${ready} ready · ${needsReview} ${needsReview === 1 ? "needs" : "need"} review`;
  return (
    <div className="rounded-xl border border-linen bg-paper/70">
      <button
        type="button"
        aria-expanded={expanded}
        className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left hover:bg-orangeSoft/30"
        onClick={onToggle}
      >
        <span className="inline-flex items-center gap-2 text-sm font-semibold text-charcoal">
          {expanded ? <ChevronDown className="h-4 w-4 text-bronze" /> : <ChevronRight className="h-4 w-4 text-bronze" />}
          {expanded ? expandedLabel : collapsedLabel}
        </span>
        <span className="text-xs text-taupe">{summary}</span>
      </button>
      {expanded ? (
        <div className="border-t border-linen p-3">
          {children}
        </div>
      ) : (
        <div className="border-t border-linen bg-ivory/35 px-4 py-2 text-xs text-taupe">
          {summary}
        </div>
      )}
    </div>
  );
}

function InternalDebugPanel({
  report,
  expanded,
  copyStatus,
  onToggle,
  onCopy,
  onDownload,
}: {
  report: ReturnType<typeof buildInternalDebugReport>;
  expanded: boolean;
  copyStatus: string;
  onToggle: () => void;
  onCopy: () => void;
  onDownload: (format: "json" | "txt") => void;
}) {
  const upload = report.upload;
  return (
    <section className="rounded-2xl border border-linen bg-paper/60 p-4 text-sm text-charcoal">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-taupe">Internal</div>
          <div className="font-semibold text-charcoal">Debug report</div>
        </div>
        <button
          type="button"
          className="btn-secondary inline-flex h-9 items-center justify-center gap-2 rounded-xl px-3 text-xs font-semibold"
          onClick={onToggle}
        >
          {expanded ? "Hide Debug" : "Debug"}
        </button>
      </div>
      {expanded ? (
        <div className="mt-4 grid gap-4 border-t border-linen pt-4">
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary inline-flex h-9 items-center gap-2 rounded-xl px-3 text-xs font-semibold" onClick={onCopy}>
              <Copy className="h-3.5 w-3.5" />
              Copy Debug Report
            </button>
            <button type="button" className="btn-secondary inline-flex h-9 items-center gap-2 rounded-xl px-3 text-xs font-semibold" onClick={() => onDownload("json")}>
              <Download className="h-3.5 w-3.5" />
              Download Debug JSON
            </button>
            <button type="button" className="btn-secondary inline-flex h-9 items-center gap-2 rounded-xl px-3 text-xs font-semibold" onClick={() => onDownload("txt")}>
              <Download className="h-3.5 w-3.5" />
              Download Debug TXT
            </button>
            {copyStatus ? <span className="self-center text-xs text-taupe">{copyStatus}</span> : null}
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-linen bg-ivory/40 p-3">
              <div className="mb-2 font-semibold">Upload summary</div>
              <DebugLine label="File" value={upload?.fileName || "No upload captured"} />
              <DebugLine label="Type" value={upload?.fileType || ""} />
              <DebugLine label="Size" value={upload?.fileSize || ""} />
              <DebugLine label="Uploaded" value={upload?.uploadedAt || ""} />
              <DebugLine label="Parser" value={upload?.parser || ""} />
              <DebugLine label="Duration" value={upload ? `${upload.parseDurationMs}ms` : ""} />
              <DebugLine label="Pages" value={upload?.pageCount ?? ""} />
              <DebugLine label="OCR" value={upload ? (upload.ocrUsed ? "yes" : "no") : ""} />
              <DebugLine label="Raw text" value={upload?.rawTextLength ?? ""} />
              <DebugLine label="Item groups" value={upload?.detectedItemGroups ?? ""} />
              <DebugLine label="Entries" value={upload?.finalProductEntries ?? report.summary.totalFinalProductEntries} />
              <DebugLine label="Ready" value={report.summary.numberReady} />
              <DebugLine label="Needs review" value={report.summary.numberNeedsReview} />
            </div>
            <div className="rounded-xl border border-linen bg-ivory/40 p-3">
              <div className="mb-2 font-semibold">Failure summary</div>
              <DebugLine label="Missing fields" value={report.failureSummary.fieldsMostCommonlyMissing.map((item) => `${item.field} (${item.count})`).join(", ") || "none"} />
              <DebugLine label="Failed enrichment" value={report.failureSummary.itemsThatFailedEnrichment.length} />
              <DebugLine label="Parser warnings" value={report.failureSummary.parserWarnings.join(" | ") || "none"} />
              <DebugLine label="Grouping warnings" value={report.failureSummary.groupingWarnings.join(" | ") || "none"} />
              <DebugLine label="Skipped rows" value={String(report.failureSummary.skippedRows)} />
              <DebugLine label="Low confidence" value={report.failureSummary.duplicateOrLowConfidenceItems.length} />
            </div>
            {report.enrichmentMetrics ? (
              <div className="rounded-xl border border-linen bg-ivory/40 p-3">
                <div className="mb-2 font-semibold">Enrichment metrics</div>
                <DebugLine label="Mode" value={report.enrichmentMetrics.mode || "unknown"} />
                <DebugLine label="Target" value={formatUsd(report.enrichmentMetrics.target_budget_usd)} />
                <DebugLine label="Hard cap" value={formatUsd(report.enrichmentMetrics.hard_budget_usd)} />
                <DebugLine label="Est. cost" value={formatUsd(report.enrichmentMetrics.estimated_cost_usd)} />
                <DebugLine label="Bravi API cost" value={formatUsd(report.enrichmentMetrics.bravi_cost_usd)} />
                <DebugLine label="Bravi searches" value={report.enrichmentMetrics.bravi_searches ?? 0} />
                <DebugLine label="Avg/item" value={formatUsd(report.enrichmentMetrics.avg_cost_per_item_usd)} />
                <DebugLine label="Paid calls" value={report.enrichmentMetrics.paid_calls ?? 0} />
                <DebugLine label="Remaining" value={formatUsd(report.enrichmentMetrics.remaining_budget_usd)} />
                <DebugLine label="Search calls" value={report.enrichmentMetrics.search_calls ?? 0} />
                <DebugLine label="Page fetches" value={report.enrichmentMetrics.page_fetches ?? 0} />
                <DebugLine label="External lookups" value={`${report.enrichmentMetrics.external_lookups ?? 0}/${report.enrichmentMetrics.external_lookups_limit ?? "?"}`} />
                <DebugLine label="Image searches" value={`${report.enrichmentMetrics.image_searches ?? 0}/${report.enrichmentMetrics.image_searches_limit ?? "?"}`} />
                <DebugLine label="Broad searches" value={report.enrichmentMetrics.broad_searches ?? 0} />
                <DebugLine label="Retries" value={`${report.enrichmentMetrics.retries ?? 0}/${report.enrichmentMetrics.retries_limit ?? "?"}`} />
                <DebugLine label="AI calls" value={`${report.enrichmentMetrics.ai_calls ?? 0}/${report.enrichmentMetrics.ai_calls_limit ?? "?"}`} />
                <DebugLine label="AI avoided" value={report.enrichmentMetrics.ai_calls_avoided ?? 0} />
                <DebugLine label="Cache hits" value={report.enrichmentMetrics.cache_hits ?? 0} />
                <DebugLine label="Cache hit rate" value={report.enrichmentMetrics.cache_hit_rate ?? 0} />
                <DebugLine label="Duplicates" value={report.enrichmentMetrics.duplicate_reuse ?? 0} />
                <DebugLine label="Cheap only" value={report.enrichmentMetrics.cheap_local_only ?? 0} />
                <DebugLine label="Skipped" value={report.enrichmentMetrics.skipped_enrichments ?? 0} />
                <DebugLine label="Budget skips" value={report.enrichmentMetrics.skipped_calls_due_budget ?? 0} />
                <DebugLine label="Fields skipped" value={report.enrichmentMetrics.fields_skipped_due_budget ?? 0} />
                <DebugLine
                  label="Most expensive"
                  value={
                    report.enrichmentMetrics.most_expensive_item
                      ? `${report.enrichmentMetrics.most_expensive_item} (${formatUsd(report.enrichmentMetrics.most_expensive_item_cost_usd)})`
                      : "none"
                  }
                />
                <DebugLine label="Duration" value={`${report.enrichmentMetrics.duration_ms ?? 0}ms`} />
              </div>
            ) : null}
          </div>

          {report.enrichmentMetrics ? (
            <details className="rounded-xl border border-linen bg-paper/70 p-3 text-xs">
              <summary className="cursor-pointer font-semibold">Cost-control trace</summary>
              <div className="mt-3 grid gap-2">
                <DebugLine label="Cost by stage" value={JSON.stringify(report.enrichmentMetrics.cost_by_stage || {})} />
                <DebugLine label="Cost by provider" value={JSON.stringify(report.enrichmentMetrics.cost_by_provider || {})} />
                <DebugLine label="Cost by field" value={JSON.stringify(report.enrichmentMetrics.cost_by_field || {})} />
                <DebugLine label="Bravi calls" value={JSON.stringify(report.enrichmentMetrics.bravi_calls || [])} />
                <DebugLine label="Paid call reasons" value={JSON.stringify(report.enrichmentMetrics.paid_call_reasons || [])} />
                <DebugLine label="Budget skipped calls" value={JSON.stringify(report.enrichmentMetrics.budget_skipped_calls || [])} />
                <DebugLine label="Budget skipped fields" value={JSON.stringify(report.enrichmentMetrics.budget_skipped_fields || [])} />
              </div>
            </details>
          ) : null}

          <div className="grid gap-3">
            {report.products.length ? report.products.map((product) => (
              <details key={`${product.index}-${product.id}`} className="rounded-xl border border-linen bg-paper/70 p-3">
                <summary className="cursor-pointer font-semibold">
                  #{product.index} {product.parsedFields["Product Name"].value || "Unnamed Item"} · {product.finalStatus || "No status"}
                </summary>
                <div className="mt-3 grid gap-3">
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-ivory p-3 text-xs text-charcoal">
                    {product.rawGroupedText || "No raw grouped text captured."}
                  </pre>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[720px] text-left text-xs">
                      <thead>
                        <tr className="text-charcoal/60">
                          <th className="border-b border-linen py-2 pr-2">Field</th>
                          <th className="border-b border-linen py-2 pr-2">Value</th>
                          <th className="border-b border-linen py-2 pr-2">Source</th>
                          <th className="border-b border-linen py-2 pr-2">Confidence</th>
                          <th className="border-b border-linen py-2 pr-2">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(product.parsedFields).map(([field, trace]) => (
                          <tr key={field}>
                            <td className="border-b border-linen/60 py-2 pr-2 font-semibold">{field}</td>
                            <td className="border-b border-linen/60 py-2 pr-2">{String(trace.value ?? "null")}</td>
                            <td className="border-b border-linen/60 py-2 pr-2">{trace.source}</td>
                            <td className="border-b border-linen/60 py-2 pr-2">{trace.confidence.toFixed(2)}</td>
                            <td className="border-b border-linen/60 py-2 pr-2">{trace.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="rounded-lg border border-linen bg-ivory/50 p-3 text-xs">
                    <div className="font-semibold text-charcoal">Enrichment trace</div>
                    <DebugLine label="Query" value={product.enrichment.query || "none"} />
                    <DebugLine label="Attempted" value={product.enrichment.attempted ? "yes" : "no"} />
                    <DebugLine label="Bravi used" value={product.enrichment.braviUsed || "no"} />
                    <DebugLine label="Bravi query" value={product.enrichment.braviQuery || "none"} />
                    <DebugLine label="Bravi cost" value={product.enrichment.braviCost || "$0.0000"} />
                    <DebugLine label="Bravi status" value={product.enrichment.braviResultStatus || "none"} />
                    <DebugLine label="Bravi fields" value={product.enrichment.braviFieldsFilled || "none"} />
                    <DebugLine label="Bravi skipped" value={product.enrichment.braviSkippedReason || "none"} />
                    <DebugLine label="Bravi calls" value={JSON.stringify(product.enrichment.braviCalls || [])} />
                    <DebugLine label="Matched URL" value={product.enrichment.matchedUrl || "none"} />
                    <DebugLine label="Source domains" value={product.enrichment.sourceDomainsTried || "none"} />
                    <DebugLine label="Selected domain" value={product.enrichment.selectedDomain || "none"} />
                    <DebugLine label="Selection reason" value={product.enrichment.sourceSelectionReason || "none"} />
                    <DebugLine label="Dimensions method" value={product.enrichment.dimensionsExtractionMethod || "none"} />
                    <DebugLine label="Image method" value={product.enrichment.imageExtractionMethod || "none"} />
                    <DebugLine label="Targeted retry" value={product.enrichment.targetedRetryStatus || "none"} />
                    <DebugLine label="Retry missing fields" value={product.enrichment.targetedRetryMissingFields || "none"} />
                    <DebugLine label="Retry filled fields" value={product.enrichment.targetedRetryFilledFields || "none"} />
                    <DebugLine label="Retry extra cost" value={product.enrichment.targetedRetryExtraCost || "$0.0000"} />
                    <DebugLine label="Retry attempts" value={JSON.stringify(product.enrichment.targetedRetryAttempts || [])} />
                    <DebugLine label="Source stored" value={product.enrichment.successfulSourceStored || "none"} />
                    <DebugLine label="Rejected URLs" value={product.enrichment.rejectedUrlsAndReasons || "none"} />
                    <DebugLine label="Failed fields" value={product.enrichment.failedFields.join(", ") || "none"} />
                    <DebugLine label="Failure reason" value={product.enrichment.failureReason || "none"} />
                    <DebugLine label="Retry count" value={product.enrichment.retryCount} />
                    <DebugLine label="Status" value={product.enrichment.status} />
                  </div>
                  <div className="rounded-lg border border-linen bg-ivory/50 p-3 text-xs">
                    <div className="font-semibold text-charcoal">Image trace</div>
                    <DebugLine label="Query" value={product.imageTrace.queryUsed || "none"} />
                    <DebugLine label="Selected" value={product.imageTrace.selectedCandidate || "none"} />
                    <DebugLine label="Source" value={product.imageTrace.sourceType || "none"} />
                    <DebugLine label="Confidence" value={product.imageTrace.finalConfidence || "none"} />
                    <DebugLine label="Upload status" value={product.imageTrace.uploadStatus || "none"} />
                    <DebugLine label="Upload failure" value={product.imageTrace.uploadFailureReason || "none"} />
                    <DebugLine label="Cloudinary URL" value={product.imageTrace.cloudinaryUrl || "none"} />
                    <DebugLine label="Cloudinary public ID" value={product.imageTrace.cloudinaryPublicId || "none"} />
                    <DebugLine label="Original URL" value={product.imageTrace.originalImageUrl || "none"} />
                    <DebugLine label="Upload debug" value={JSON.stringify(product.imageTrace.uploadDebug)} />
                    <DebugLine label="Candidates" value={JSON.stringify(product.imageTrace.candidatesFound)} />
                    <DebugLine
                      label="Rejected"
                      value={
                        typeof product.imageTrace.rejectedCandidates === "string"
                          ? product.imageTrace.rejectedCandidates || "none"
                          : JSON.stringify(product.imageTrace.rejectedCandidates)
                      }
                    />
                  </div>
                </div>
              </details>
            )) : (
              <div className="rounded-xl border border-dashed border-linen bg-ivory/40 p-4 text-xs text-taupe">
                No product rows available yet.
              </div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function DebugLine({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="grid grid-cols-[130px_1fr] gap-2 py-0.5 text-xs">
      <span className="font-semibold text-charcoal/60">{label}</span>
      <span className="break-words text-charcoal">{value === null || value === undefined || value === "" ? "-" : String(value)}</span>
    </div>
  );
}

function Panel({
  step,
  title,
  subtitle,
  accent = false,
  children,
}: {
  step?: string;
  title: string;
  subtitle: string;
  accent?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={`rounded-2xl border bg-paper/72 p-5 transition-colors sm:p-6 ${
        accent ? "border-orangeBorder/70 hover:bg-orangeSoft/10" : "border-linen"
      }`}
    >
      <div className="mb-5">
        <div>
          <div className="flex items-center gap-3">
            {step ? (
              <span className="grid h-8 w-8 place-items-center rounded-full border border-orangeBorder bg-orangeSoft text-sm font-semibold text-bronze">
                {step}
              </span>
            ) : null}
            <h2 className="text-xl font-semibold tracking-normal text-charcoal">{title}</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-charcoal/60">{subtitle}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-semibold uppercase text-charcoal/55">{label}</span>
      {children}
    </label>
  );
}

function SettingsDialog({
  entries,
  form,
  status,
  busy,
  websiteTheme,
  accentColor,
  uiDensity,
  animationPreference,
  useWebEnrichment,
  enrichmentMode,
  enrichmentPriority,
  missingFieldRetryMode,
  missingFieldRetryMaxRunCost,
  missingFieldRetryMaxItemCost,
  allowReplaceLowConfidenceData,
  autoRetryFailedItems,
  measurementUnit,
  itemDebugMode,
  includeLowConfidenceImages,
  budgetPreview,
  enrichmentMetrics,
  usage,
  debugCopyStatus,
  isClosing,
  onClose,
  onChangeTheme,
  onChangeAccent,
  onChangeDensity,
  onChangeAnimationPreference,
  onChangeUseWebEnrichment,
  onChangeEnrichmentMode,
  onChangeEnrichmentPriority,
  onChangeMissingFieldRetryMode,
  onChangeMissingFieldRetryMaxRunCost,
  onChangeMissingFieldRetryMaxItemCost,
  onChangeAllowReplaceLowConfidenceData,
  onChangeAutoRetry,
  onChangeMeasurementUnit,
  onChangeItemDebugMode,
  onChangeIncludeLowConfidenceImages,
  onChangeForm,
  onSave,
  onEdit,
  onDelete,
  onReset,
  onImportWebsites,
  onExportWebsites,
  onCopyDebug,
  onDownloadDebug,
}: {
  entries: PreferredWebsiteEntry[];
  form: { keyword: string; url: string; notes: string; id: string };
  status: string;
  busy: boolean;
  websiteTheme: WebsiteThemeId;
  accentColor: AccentColorId;
  uiDensity: UiDensityId;
  animationPreference: AnimationPreferenceId;
  useWebEnrichment: boolean;
  enrichmentMode: "fast" | "standard" | "deep" | "manual_retry";
  enrichmentPriority: EnrichmentPriorityId;
  missingFieldRetryMode: MissingFieldRetryModeId;
  missingFieldRetryMaxRunCost: string;
  missingFieldRetryMaxItemCost: string;
  allowReplaceLowConfidenceData: boolean;
  autoRetryFailedItems: boolean;
  measurementUnit: MeasurementUnitId;
  itemDebugMode: boolean;
  includeLowConfidenceImages: boolean;
  budgetPreview: ReturnType<typeof enrichmentBudgetPreview>;
  enrichmentMetrics: Record<string, unknown> | null;
  usage: {
    successRate: number | null;
    imageSuccessRate: number | null;
    dimensionSuccessRate: number | null;
    averageCostPerRun: number;
    averageCostPerItem: number;
    topWebsites: PreferredWebsiteEntry[];
    providerCosts: { provider: string; cost: number }[];
  };
  debugCopyStatus: string;
  isClosing: boolean;
  onClose: () => void;
  onChangeTheme: (theme: WebsiteThemeId) => void;
  onChangeAccent: (accent: AccentColorId) => void;
  onChangeDensity: (density: UiDensityId) => void;
  onChangeAnimationPreference: (preference: AnimationPreferenceId) => void;
  onChangeUseWebEnrichment: (enabled: boolean) => void;
  onChangeEnrichmentMode: (mode: "fast" | "standard" | "deep" | "manual_retry") => void;
  onChangeEnrichmentPriority: (priority: EnrichmentPriorityId) => void;
  onChangeMissingFieldRetryMode: (mode: MissingFieldRetryModeId) => void;
  onChangeMissingFieldRetryMaxRunCost: (value: string) => void;
  onChangeMissingFieldRetryMaxItemCost: (value: string) => void;
  onChangeAllowReplaceLowConfidenceData: (enabled: boolean) => void;
  onChangeAutoRetry: (enabled: boolean) => void;
  onChangeMeasurementUnit: (unit: MeasurementUnitId) => void;
  onChangeItemDebugMode: (enabled: boolean) => void;
  onChangeIncludeLowConfidenceImages: (enabled: boolean) => void;
  onChangeForm: (form: { keyword: string; url: string; notes: string; id: string }) => void;
  onSave: () => void;
  onEdit: (entry: PreferredWebsiteEntry) => void;
  onDelete: (entry: PreferredWebsiteEntry) => void;
  onReset: () => void;
  onImportWebsites: (file: File | null | undefined) => void;
  onExportWebsites: () => void;
  onCopyDebug: () => void;
  onDownloadDebug: (format: "json" | "txt") => void;
}) {
  const importInputRef = useRef<HTMLInputElement>(null);
  const selectedPriority = enrichmentPriorityOptions.find((option) => option.id === enrichmentPriority) || enrichmentPriorityOptions[0];
  const selectedRetryMode = missingFieldRetryModeOptions.find((option) => option.id === missingFieldRetryMode) || missingFieldRetryModeOptions[1];
  const websiteSuccessTotal = entries.reduce((total, entry) => total + Number(entry.success_count || 0), 0);
  const websiteFailureTotal = entries.reduce((total, entry) => total + Number(entry.failure_count || 0), 0);
  const websiteSuccessRate =
    websiteSuccessTotal + websiteFailureTotal > 0
      ? websiteSuccessTotal / (websiteSuccessTotal + websiteFailureTotal)
      : null;

  return (
    <div className={`settings-overlay fixed inset-0 z-50 grid place-items-center bg-black/35 px-4 py-6${isClosing ? " settings-overlay-closing" : ""}`}>
      <div className={`settings-dialog max-h-[90vh] w-full max-w-5xl overflow-auto rounded-2xl border border-linen bg-paper p-5 shadow-xl sm:p-6${isClosing ? " settings-dialog-closing" : ""}`}>
        <div className="flex items-start justify-between gap-4 border-b border-linen pb-4">
          <div>
            <div className="text-xl font-semibold text-charcoal">Settings</div>
            <div className="mt-1 text-sm text-taupe">Simple preferences first. Advanced controls stay tucked away until you need them.</div>
          </div>
          <button type="button" className="btn-secondary inline-flex h-9 w-9 items-center justify-center rounded-xl" onClick={onClose} aria-label="Close settings">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-5 grid gap-4">
          <SettingsSection
            title="Appearance"
            description="Keep the workspace comfortable without changing your data."
          >
            <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
              <div className="grid gap-4 rounded-xl border border-linen bg-ivory/35 p-4">
                <div>
                  <div className="mb-2 text-xs font-semibold uppercase text-charcoal/55">Theme</div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {websiteThemeOptions.map((option) => (
                      <ThemePreviewCard
                        key={option.id}
                        theme={option.id}
                        label={option.label}
                        active={websiteTheme === option.id}
                        onSelect={() => onChangeTheme(option.id)}
                      />
                    ))}
                  </div>
                </div>
                <div>
                  <div className="mb-2 text-xs font-semibold uppercase text-charcoal/55">Accent</div>
                  <div className="flex flex-wrap gap-2">
                    {accentColorOptions.map((option) => (
                      <button
                        key={option.id}
                        type="button"
                        className={`btn-secondary inline-flex h-9 items-center gap-2 rounded-xl px-3 text-xs font-semibold ${
                          accentColor === option.id ? "border-orangeBorder bg-orangeSoft text-bronze" : ""
                        }`}
                        onClick={() => onChangeAccent(option.id)}
                      >
                        <span className="h-3 w-3 rounded-full border border-linen" style={{ backgroundColor: option.color }} />
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="grid gap-3">
                <SettingChoiceGroup
                  label="UI density"
                  options={uiDensityOptions}
                  value={uiDensity}
                  onChange={(value) => onChangeDensity(value as UiDensityId)}
                />
                <SettingChoiceGroup
                  label="Animation"
                  options={animationPreferenceOptions}
                  value={animationPreference}
                  onChange={(value) => onChangeAnimationPreference(value as AnimationPreferenceId)}
                />
              </div>
            </div>
          </SettingsSection>

          <SettingsSection
            title="Preferred Brands & Websites"
            description="Trusted brand and vendor sources are checked before broad search."
          >
            <div className="grid gap-3 rounded-xl border border-orangeBorder/40 bg-orangeSoft/15 p-4 md:grid-cols-[1fr_1.2fr]">
              <Field label="Brand / keyword">
                <input
                  className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                  value={form.keyword}
                  onChange={(event) => onChangeForm({ ...form, keyword: event.target.value })}
                  placeholder="Sub-Zero"
                />
              </Field>
              <Field label="Preferred website URL">
                <input
                  className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                  value={form.url}
                  onChange={(event) => onChangeForm({ ...form, url: event.target.value })}
                  placeholder="https://www.subzero-wolf.com/"
                />
              </Field>
              <div className="md:col-span-2">
                <Field label="Notes">
                  <textarea
                    className="input-surface min-h-20 w-full resize-none rounded-xl p-3 text-sm text-charcoal"
                    value={form.notes}
                    onChange={(event) => onChangeForm({ ...form, notes: event.target.value })}
                    placeholder="Optional notes for internal use"
                  />
                </Field>
              </div>
              <div className="flex flex-wrap items-center gap-2 md:col-span-2">
                <button type="button" className="btn-primary inline-flex h-10 items-center justify-center rounded-xl px-4 text-sm font-semibold" disabled={busy} onClick={onSave}>
                  {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  {form.id ? "Save Changes" : "Add Website"}
                </button>
                {form.id ? (
                  <button type="button" className="btn-secondary inline-flex h-10 items-center justify-center rounded-xl px-4 text-sm font-semibold" disabled={busy} onClick={onReset}>
                    Cancel Edit
                  </button>
                ) : null}
                {status ? <span className="text-sm text-taupe">{status}</span> : null}
              </div>
            </div>

            <div className="grid gap-3 rounded-xl border border-linen bg-ivory/35 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
              <div>
                <div className="text-sm font-semibold text-charcoal">Import / export preferences</div>
                <div className="mt-1 text-xs leading-5 text-taupe">
                  Move trusted website preferences between browsers or projects.
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold" disabled={busy || entries.length === 0} onClick={onExportWebsites}>
                  Export
                </button>
                <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold" disabled={busy} onClick={() => importInputRef.current?.click()}>
                  Import
                </button>
                <input
                  ref={importInputRef}
                  className="hidden"
                  type="file"
                  accept="application/json,.json"
                  onChange={(event) => {
                    onImportWebsites(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </div>
            </div>

            <div className="overflow-x-auto rounded-xl border border-linen bg-paper">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="bg-ivory text-xs uppercase text-charcoal/60">
                  <tr>
                    <th className="px-3 py-3">Priority</th>
                    <th className="px-3 py-3">Brand/Keyword</th>
                    <th className="px-3 py-3">Website</th>
                    <th className="px-3 py-3">Notes</th>
                    <th className="px-3 py-3">Results</th>
                    <th className="px-3 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.length ? entries.map((entry) => (
                    <tr key={entry.id} className="border-t border-linen align-top">
                      <td className="px-3 py-3 text-xs font-semibold text-taupe">
                        {Number(entry.success_count || 0) > 0 ? "High" : "Standard"}
                      </td>
                      <td className="px-3 py-3 font-semibold text-charcoal">{entry.keyword}</td>
                      <td className="px-3 py-3">
                        <div className="text-charcoal">{entry.domain}</div>
                        <div className="max-w-[260px] truncate text-xs text-taupe">{entry.url}</div>
                      </td>
                      <td className="px-3 py-3 text-taupe">{entry.notes || ""}</td>
                      <td className="px-3 py-3 text-xs text-taupe">
                        <div>{entry.success_count || 0} success · {entry.failure_count || 0} failed</div>
                        <div>{entry.last_status || "not used yet"}</div>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap gap-2">
                          <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold" disabled={busy} onClick={() => onEdit(entry)}>
                            Edit
                          </button>
                          <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold text-clay" disabled={busy} onClick={() => onDelete(entry)}>
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td className="px-3 py-6 text-center text-sm text-taupe" colSpan={6}>
                        No preferred websites saved yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="text-xs text-taupe">
              Brand priority is based on past successful matches. Websites with successful results are tried first.
            </div>
          </SettingsSection>

          <SettingsSection
            title="Enrichment Preferences"
            description="Simple defaults for how missing product details are filled."
          >
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="grid gap-3 rounded-xl border border-linen bg-ivory/35 p-4">
                <Field label="Search depth">
                  <select
                    className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                    value={enrichmentMode}
                    onChange={(event) => onChangeEnrichmentMode(event.target.value as typeof enrichmentMode)}
                    disabled={!useWebEnrichment}
                  >
                    <option value="fast">Fast</option>
                    {INTERNAL_DEBUG_ENABLED ? <option value="standard">Balanced</option> : null}
                    {INTERNAL_DEBUG_ENABLED ? <option value="deep">Deep</option> : null}
                    {INTERNAL_DEBUG_ENABLED ? <option value="manual_retry">Retry failed items</option> : null}
                  </select>
                </Field>
                <label className="flex items-center gap-2 text-sm text-taupe">
                  <input
                    type="checkbox"
                    checked={useWebEnrichment}
                    onChange={(event) => onChangeUseWebEnrichment(event.target.checked)}
                    className="h-4 w-4 accent-bronze"
                  />
                  Search websites for missing details
                </label>
              </div>

              <div className="grid gap-3 rounded-xl border border-linen bg-ivory/35 p-4">
                <Field label="Priority">
                  <select
                    className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                    value={enrichmentPriority}
                    onChange={(event) => onChangeEnrichmentPriority(event.target.value as EnrichmentPriorityId)}
                  >
                    {enrichmentPriorityOptions.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="text-xs leading-5 text-taupe">{selectedPriority.description}</div>
                <Field label="Missing-field retry">
                  <select
                    className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                    value={missingFieldRetryMode}
                    onChange={(event) => onChangeMissingFieldRetryMode(event.target.value as MissingFieldRetryModeId)}
                    disabled={!useWebEnrichment}
                  >
                    {missingFieldRetryModeOptions.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="text-xs leading-5 text-taupe">{selectedRetryMode.description}</div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Field label="Max retry cost / run">
                    <input
                      className="input-surface h-10 w-full rounded-xl px-3 text-sm text-charcoal"
                      value={missingFieldRetryMaxRunCost}
                      onChange={(event) => onChangeMissingFieldRetryMaxRunCost(event.target.value)}
                      inputMode="decimal"
                      placeholder="0.04"
                      disabled={!useWebEnrichment}
                    />
                  </Field>
                  <Field label="Max retry cost / item">
                    <input
                      className="input-surface h-10 w-full rounded-xl px-3 text-sm text-charcoal"
                      value={missingFieldRetryMaxItemCost}
                      onChange={(event) => onChangeMissingFieldRetryMaxItemCost(event.target.value)}
                      inputMode="decimal"
                      placeholder="0.006"
                      disabled={!useWebEnrichment}
                    />
                  </Field>
                </div>
                <label className="flex items-start gap-2 text-xs text-taupe">
                  <input
                    type="checkbox"
                    checked={allowReplaceLowConfidenceData}
                    onChange={(event) => onChangeAllowReplaceLowConfidenceData(event.target.checked)}
                    className="mt-0.5 h-4 w-4 accent-bronze"
                    disabled={!useWebEnrichment}
                  />
                  <span>Allow replacing low-confidence data during manual missing-field retry.</span>
                </label>
              </div>

              <div className="grid gap-3 rounded-xl border border-linen bg-ivory/35 p-4">
                <label className="flex items-start gap-2 text-sm text-taupe">
                  <input
                    type="checkbox"
                    checked={autoRetryFailedItems}
                    onChange={(event) => onChangeAutoRetry(event.target.checked)}
                    className="mt-1 h-4 w-4 accent-bronze"
                    disabled={!INTERNAL_DEBUG_ENABLED}
                  />
                  <span>
                    <span className="block font-semibold text-charcoal">Auto-retry failed items</span>
                    <span className="text-xs">{INTERNAL_DEBUG_ENABLED ? "Uses the retry pass when enrichment runs." : "Available for internal review mode."}</span>
                  </span>
                </label>
                <Field label="Measurement unit">
                  <select
                    className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                    value={measurementUnit}
                    onChange={(event) => onChangeMeasurementUnit(event.target.value as MeasurementUnitId)}
                  >
                    <option value="imperial">Inches</option>
                    <option value="metric">Centimeters</option>
                  </select>
                </Field>
              </div>
            </div>
          </SettingsSection>

          <details className="rounded-xl border border-linen bg-ivory/25">
            <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-charcoal">
              Advanced Settings
            </summary>
            <div className="grid gap-4 border-t border-linen p-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <AdvancedSettingCard title="Cost controls" value={`${budgetPreview.label} · cap ${formatUsd(budgetPreview.hard)}`} />
                <AdvancedSettingCard title="Cache behavior" value={`Cache hits ${String(enrichmentMetrics?.cache_hits ?? 0)} · paid calls ${String(enrichmentMetrics?.paid_calls ?? 0)}`} />
                <AdvancedSettingCard title="Confidence thresholds" value="Review flags remain visible on product rows." />
                <AdvancedSettingCard title="Provider controls" value={usage.providerCosts.length ? usage.providerCosts.map((item) => item.provider).join(", ") : "No provider usage yet"} />
                <AdvancedSettingCard title="Debug & Diagnostics" value={itemDebugMode ? "Item metadata visible on rows" : "Off for everyday review"} />
                <AdvancedSettingCard title="Extraction tracing" value={itemDebugMode ? "Available in item rows and report export" : "Hidden"} />
                <AdvancedSettingCard title="Advanced export formatting" value={includeLowConfidenceImages ? "Low-confidence ZIP images included" : "Low-confidence ZIP images excluded"} />
              </div>
              <div className="rounded-xl border border-linen bg-paper p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-charcoal">Debug &amp; Diagnostics</div>
                    <div className="mt-1 text-xs leading-5 text-taupe">
                      Optional row-level metadata for troubleshooting source quality, confidence, cost, and incomplete enrichment.
                    </div>
                  </div>
                  <label className="flex items-center gap-2 rounded-xl border border-linen bg-ivory/45 px-3 py-2 text-xs font-semibold text-charcoal">
                    <input
                      type="checkbox"
                      checked={itemDebugMode}
                      onChange={(event) => onChangeItemDebugMode(event.target.checked)}
                      className="h-4 w-4 accent-bronze"
                    />
                    Enable Item Debug Mode
                  </label>
                </div>
                {itemDebugMode ? (
                  <div className="mt-3 flex flex-wrap gap-2 border-t border-linen pt-3">
                    <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold" onClick={onCopyDebug}>
                      Copy Debug Summary
                    </button>
                    <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold" onClick={() => onDownloadDebug("json")}>
                      Download JSON
                    </button>
                    <button type="button" className="btn-secondary h-9 rounded-xl px-3 text-xs font-semibold" onClick={() => onDownloadDebug("txt")}>
                      Download TXT
                    </button>
                    {debugCopyStatus ? <span className="self-center text-xs text-taupe">{debugCopyStatus}</span> : null}
                  </div>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-2">
                <label className="flex items-center gap-2 rounded-xl border border-linen bg-paper px-3 py-2 text-xs font-semibold text-taupe">
                  <input
                    type="checkbox"
                    checked={includeLowConfidenceImages}
                    onChange={(event) => onChangeIncludeLowConfidenceImages(event.target.checked)}
                    className="h-4 w-4 accent-bronze"
                  />
                  Include low-confidence ZIP images
                </label>
              </div>
            </div>
          </details>

          <SettingsSection
            title="Analytics & Usage"
            description="A quick read on how enrichment has been performing in this browser session."
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <MetricCard label="Average success" value={formatPercent(usage.successRate)} />
              <MetricCard label="Image success" value={formatPercent(usage.imageSuccessRate)} />
              <MetricCard label="Dimension success" value={formatPercent(usage.dimensionSuccessRate)} />
              <MetricCard label="Average cost/run" value={formatUsd(usage.averageCostPerRun)} />
              <MetricCard label="Average cost/item" value={formatUsd(usage.averageCostPerItem)} />
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-xl border border-linen bg-ivory/35 p-4">
                <div className="text-sm font-semibold text-charcoal">Most successful websites</div>
                <div className="mt-3 grid gap-2 text-sm text-taupe">
                  {usage.topWebsites.length ? usage.topWebsites.map((entry) => (
                    <div key={entry.id} className="flex items-center justify-between gap-3">
                      <span className="truncate">{entry.domain || entry.url}</span>
                      <span className="text-xs font-semibold text-charcoal">{entry.success_count || 0} success</span>
                    </div>
                  )) : <span>No website results yet.</span>}
                </div>
              </div>
              <div className="rounded-xl border border-linen bg-ivory/35 p-4">
                <div className="text-sm font-semibold text-charcoal">Provider usage</div>
                <div className="mt-3 grid gap-2 text-sm text-taupe">
                  {usage.providerCosts.length ? usage.providerCosts.map((item) => (
                    <div key={item.provider} className="flex items-center justify-between gap-3">
                      <span className="truncate">{item.provider}</span>
                      <span className="text-xs font-semibold text-charcoal">{formatUsd(item.cost)}</span>
                    </div>
                  )) : <span>No provider usage yet.</span>}
                </div>
              </div>
            </div>
            <div className="text-xs text-taupe">
              Preferred website success rate: {formatPercent(websiteSuccessRate)}
            </div>
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

function SettingsSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <section className="grid gap-4 rounded-xl border border-linen bg-paper p-4">
      <div>
        <h3 className="text-base font-semibold text-charcoal">{title}</h3>
        <p className="mt-1 text-sm leading-6 text-taupe">{description}</p>
      </div>
      {children}
    </section>
  );
}

function SettingChoiceGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: string; label: string; description: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="rounded-xl border border-linen bg-ivory/35 p-4">
      <div className="mb-2 text-xs font-semibold uppercase text-charcoal/55">{label}</div>
      <div className="grid gap-2">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            className={`btn-secondary rounded-xl px-3 py-2 text-left text-sm ${value === option.id ? "border-orangeBorder bg-orangeSoft text-bronze" : ""}`}
            onClick={() => onChange(option.id)}
          >
            <span className="block font-semibold">{option.label}</span>
            <span className="mt-0.5 block text-xs font-normal text-taupe">{option.description}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function AdvancedSettingCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl border border-linen bg-paper p-3">
      <div className="text-xs font-semibold uppercase text-charcoal/55">{title}</div>
      <div className="mt-2 text-sm leading-5 text-charcoal">{value}</div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-linen bg-ivory/35 p-4">
      <div className="text-2xl font-semibold text-charcoal">{value}</div>
      <div className="mt-1 text-xs font-semibold uppercase text-charcoal/55">{label}</div>
    </div>
  );
}

function ThemePreviewCard({
  theme,
  label,
  active,
  onSelect,
}: {
  theme: WebsiteThemeId;
  label: string;
  active: boolean;
  onSelect: () => void;
}) {
  const palette = themePreviewPalettes[theme];
  return (
    <button
      type="button"
      className={`rounded-xl border p-3 text-left transition ${
        active ? "border-orangeBorder bg-orangeSoft/35" : "border-linen bg-paper/70 hover:border-orangeBorder"
      }`}
      onClick={onSelect}
      aria-pressed={active}
    >
      <div className="overflow-hidden rounded-lg border" style={{ backgroundColor: palette.background, borderColor: palette.border }}>
        <div className="space-y-2 p-3">
          <div className="h-3 w-16 rounded-full" style={{ backgroundColor: palette.text }} />
          <div className="rounded-md border p-2" style={{ backgroundColor: palette.surface, borderColor: palette.border }}>
            <div className="h-2 w-20 rounded-full" style={{ backgroundColor: palette.text }} />
            <div className="mt-2 h-2 w-14 rounded-full" style={{ backgroundColor: palette.muted }} />
          </div>
          <div className="h-2 w-full rounded-full bg-bronze" />
        </div>
      </div>
      <div className="mt-2 text-xs font-semibold text-charcoal">{label}</div>
    </button>
  );
}

function ProductImageLightbox({ preview, onClose }: { preview: ImagePreviewState; onClose: () => void }) {
  const [zoomed, setZoomed] = useState(false);
  const [detectedResolution, setDetectedResolution] = useState(preview.resolution);
  const resolution = detectedResolution || preview.resolution || "Resolution unavailable";

  return (
    <div className="image-lightbox-overlay fixed inset-0 z-50 grid place-items-center bg-black/70 px-4 py-6" onClick={onClose}>
      <div className="image-lightbox-panel max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-2xl border border-linen bg-paper shadow-xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-linen p-4">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-charcoal">{preview.productName}</div>
            <div className="mt-1 truncate text-xs text-taupe">
              {[preview.sourceLabel, preview.sourceDomain, resolution].filter(Boolean).join(" · ") || "Product image"}
            </div>
          </div>
          <button type="button" className="btn-secondary inline-flex h-9 w-9 items-center justify-center rounded-xl" onClick={onClose} aria-label="Close image preview">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="grid gap-4 p-4">
          <button
            type="button"
            className={`grid max-h-[65vh] place-items-center overflow-auto rounded-xl bg-ivory ${zoomed ? "cursor-zoom-out" : "cursor-zoom-in"}`}
            onClick={() => setZoomed((current) => !current)}
            aria-label={zoomed ? "Zoom out product image" : "Zoom in product image"}
          >
            <img
              src={preview.url}
              alt={preview.productName}
              className={`transition-transform duration-200 ${zoomed ? "max-h-none w-auto max-w-none scale-125" : "max-h-[65vh] w-full object-contain"}`}
              onLoad={(event) => {
                const image = event.currentTarget;
                if (image.naturalWidth && image.naturalHeight) {
                  setDetectedResolution(`${image.naturalWidth} x ${image.naturalHeight} px`);
                }
              }}
            />
          </button>
          <div className="grid gap-3 rounded-xl border border-linen bg-ivory/35 p-3 text-xs text-taupe sm:grid-cols-3">
            <div className="min-w-0">
              <div className="font-semibold uppercase text-charcoal/55">Website</div>
              <div className="mt-1 truncate text-charcoal">{preview.sourceDomain || domainFromUrl(preview.url) || "Unknown"}</div>
            </div>
            <div className="min-w-0">
              <div className="font-semibold uppercase text-charcoal/55">Source</div>
              <div className="mt-1 truncate text-charcoal">{preview.sourceLabel || "Product image"}</div>
            </div>
            <div className="min-w-0">
              <div className="font-semibold uppercase text-charcoal/55">Resolution</div>
              <div className="mt-1 truncate text-charcoal">{resolution}</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-taupe">
            <span className="min-w-0 flex-1 truncate">{preview.url}</span>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-secondary inline-flex h-9 items-center gap-2 rounded-xl px-3 text-xs font-semibold" onClick={() => setZoomed((current) => !current)}>
                <Maximize2 className="h-3.5 w-3.5" />
                {zoomed ? "Fit image" : "Zoom image"}
              </button>
              <a className="btn-secondary inline-flex h-9 items-center rounded-xl px-3 text-xs font-semibold" href={preview.url} target="_blank" rel="noreferrer">
                Open image in new tab
              </a>
            {preview.sourcePage ? (
              <a className="btn-secondary inline-flex h-9 items-center rounded-xl px-3 text-xs font-semibold" href={preview.sourcePage} target="_blank" rel="noreferrer">
                Open source page
              </a>
            ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function VendorCallDialog({
  state,
  busy,
  onChange,
  onClose,
  onGenerateScript,
}: {
  state: {
    row: IntakeRow;
    missingFields: string[];
    phoneNumber: string;
    customGoal: string;
    script: string;
  };
  busy: boolean;
  onChange: (state: {
    row: IntakeRow;
    missingFields: string[];
    phoneNumber: string;
    customGoal: string;
    script: string;
  }) => void;
  onClose: () => void;
  onGenerateScript: () => void;
}) {
  const [providerEnabled, setProviderEnabled] = useState(false);
  const [providerMessage, setProviderMessage] = useState("Call provider not configured yet.");
  const [callStatus, setCallStatus] = useState("");
  const [callId, setCallId] = useState("");
  const [recordingUrl, setRecordingUrl] = useState("");
  const [suggestions, setSuggestions] = useState<Record<string, string>>({});
  const [refreshingCall, setRefreshingCall] = useState(false);
  const [startingCall, setStartingCall] = useState(false);
  const contextRows = [
    ["Location", rowText(state.row, "Room")],
    ["Supplier", rowText(state.row, "Supplier")],
    ["Category", rowText(state.row, "Product Category")],
    ["Product URL", rowText(state.row, "Product URL")],
    ["Image URL", rowText(state.row, "Image URL")],
    ["Dimensions", rowText(state.row, "Dimensions")],
    ["Finish", rowText(state.row, "Finish / Color")],
    ["Confidence", rowText(state.row, "confidence") || rowText(state.row, "Product Resolution Confidence")],
    ["Suggested Action", rowText(state.row, "Suggested Action")],
    ["Notes", cleanNotes(rowText(state.row, "Notes"))],
  ].filter(([, value]) => value.trim());

  useEffect(() => {
    let cancelled = false;
    fetchVendorCallStatus()
      .then((status) => {
        if (cancelled) return;
        setProviderEnabled(Boolean(status.enabled));
        setProviderMessage(
          status.enabled
            ? `Provider: ${status.provider}${status.agent_name ? ` / ${status.agent_name}` : ""}`
            : status.message || "Call provider not configured yet.",
        );
      })
      .catch(() => {
        if (cancelled) return;
        setProviderEnabled(false);
        setProviderMessage("Call provider not configured yet.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleStartCall() {
    setStartingCall(true);
    setCallStatus("Calling vendor...");
    setCallId("");
    setRecordingUrl("");
    setSuggestions({});
    try {
      const response = await startVendorCall({
        row: state.row,
        missingFields: state.missingFields,
        phoneNumber: state.phoneNumber,
        customGoal: state.customGoal,
      });
      const statusLabel = response.status === "call_started" ? "call_started" : response.status;
      setCallId(response.call_id || "");
      setCallStatus([statusLabel, response.message].filter(Boolean).join(": "));
    } catch (error) {
      setCallStatus(error instanceof Error ? `error: ${error.message}` : "error");
    } finally {
      setStartingCall(false);
    }
  }

  async function handleRefreshCall() {
    if (!callId) return;
    setRefreshingCall(true);
    try {
      const response = await refreshVendorCall({
        callId,
        missingFields: state.missingFields,
      });
      const label = [response.status, response.provider_status || response.queue_status].filter(Boolean).join(" / ");
      setCallStatus([label, response.message].filter(Boolean).join(": "));
      setRecordingUrl(response.recording_url || "");
      setSuggestions(response.extracted_values || {});
    } catch (error) {
      setCallStatus(error instanceof Error ? `error: ${error.message}` : "error");
    } finally {
      setRefreshingCall(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-4 py-6">
      <div className="glass-panel w-full max-w-xl rounded-lg p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-charcoal">Call Vendor</h2>
            <p className="mt-1 text-sm text-taupe">Prepare a script to retrieve missing product details.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn-secondary grid h-8 w-8 place-items-center rounded-xl text-taupe hover:bg-ivory"
            aria-label="Close vendor call dialog"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="glass-tile mt-5 grid gap-3 rounded-lg p-3 text-sm">
          <Detail label="Product Name" value={rowText(state.row, "Product Name") || "Missing"} />
          <Detail label="Brand" value={rowText(state.row, "Brand") || "Missing"} />
          <Detail label="Model/SKU" value={rowText(state.row, "Model/SKU") || "Not provided"} />
          <Detail label="Missing Field Name" value={state.missingFields.join(", ")} />
          <div className="border-t border-linen pt-3">
            <div className="mb-2 text-xs font-semibold uppercase text-charcoal/50">Current Row Context</div>
            {contextRows.length ? (
              <div className="grid gap-2">
                {contextRows.map(([label, value]) => (
                  <Detail key={label} label={label} value={value} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-taupe">No additional row context yet.</p>
            )}
          </div>
        </div>

        <div className="mt-4 grid gap-4">
          <Field label="Phone Number">
            <input
              className="input-surface h-10 w-full rounded-xl px-3 text-sm"
              value={state.phoneNumber}
              onChange={(event) => onChange({ ...state, phoneNumber: event.target.value })}
              placeholder="Vendor phone number"
            />
          </Field>
          <Field label="Call Goal">
            <textarea
              className="input-surface min-h-20 w-full resize-none rounded-xl px-3 py-2 text-sm"
              value={state.customGoal}
              onChange={(event) => onChange({ ...state, customGoal: event.target.value })}
              placeholder="Get the missing dimensions for this product."
            />
          </Field>
        </div>

        {state.script ? (
          <div className="mt-4 rounded-lg border border-orangeBorder bg-orangeSoft p-3">
            <div className="mb-2 text-xs font-semibold uppercase text-bronze">Generated Call Script Preview</div>
            <p className="whitespace-pre-wrap text-sm leading-6 text-charcoal">{state.script}</p>
          </div>
        ) : null}

        <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-end">
          <button
            type="button"
            onClick={onGenerateScript}
            disabled={!state.phoneNumber.trim() || busy}
            className="btn-primary inline-flex h-10 items-center justify-center rounded-xl px-4 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
          >
            {busy ? "Generating..." : "Generate Call Script"}
          </button>
          <div className="text-center sm:text-left">
            <button
              type="button"
              disabled={!providerEnabled || !state.phoneNumber.trim() || !state.script || startingCall}
              onClick={handleStartCall}
              className="btn-secondary inline-flex h-10 items-center justify-center rounded-xl px-4 text-sm font-semibold text-taupe hover:bg-paper disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/65"
              title={
                providerEnabled
                  ? state.script
                    ? "Start AI Call"
                    : "Generate and review the call script first."
                  : "Call provider is not configured yet."
              }
            >
              {startingCall ? "Starting..." : "Start AI Call"}
            </button>
            <p className="mt-1 text-[11px] text-taupe">{providerMessage}</p>
          </div>
        </div>
        {callStatus ? (
          <div className="mt-3 rounded-lg border border-linen bg-ivory px-3 py-2 text-sm text-charcoal">
            <div>{callStatus}</div>
            {callId ? <div className="mt-1 text-xs text-taupe">Call ID: {callId}</div> : null}
            {recordingUrl ? (
              <a
                className="mt-1 block text-xs font-semibold text-bronze hover:text-orangeHover"
                href={recordingUrl}
                target="_blank"
                rel="noreferrer"
              >
                Open recording
              </a>
            ) : null}
            {callId ? (
              <button
                type="button"
                onClick={handleRefreshCall}
                disabled={refreshingCall}
                className="mt-3 inline-flex h-8 items-center justify-center rounded-lg border border-linen bg-paper px-3 text-xs font-semibold text-taupe hover:bg-ivory disabled:cursor-not-allowed disabled:text-taupe/60"
              >
                {refreshingCall ? "Refreshing..." : "Refresh Status"}
              </button>
            ) : null}
          </div>
        ) : null}
        {Object.keys(suggestions).length ? (
          <div className="mt-3 rounded-lg border border-orangeBorder bg-orangeSoft p-3">
            <div className="mb-2 text-xs font-semibold uppercase text-bronze">Review Suggestions</div>
            <div className="grid gap-2 text-sm text-charcoal">
              {Object.entries(suggestions).map(([field, value]) => (
                <Detail key={field} label={field} value={value} />
              ))}
            </div>
            <p className="mt-2 text-xs text-taupe">Suggestions are review-only and will not update the row automatically.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[130px_1fr]">
      <div className="text-xs font-semibold uppercase text-charcoal/50">{label}</div>
      <div className="text-sm text-charcoal">{value}</div>
    </div>
  );
}

function MissingInputField({
  field,
  value,
  onChange,
  onVendorCall,
}: {
  field: string;
  value: string;
  onChange: (value: string) => void;
  onVendorCall: () => void;
}) {
  const label =
    field === "Brand"
      ? "Brand / Manufacturer"
      : field === "Supplier"
        ? "Supplier / Who Bought From"
        : field;

  return (
    <label className="grid gap-1">
      <span className="text-xs font-semibold uppercase text-charcoal/50">{label}</span>
      <div className="flex items-center gap-2">
        <input
          className="input-surface h-10 min-w-0 flex-1 rounded-xl px-3 text-sm"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={missingFieldPlaceholders[field]}
          type={field === "Quantity" ? "number" : "text"}
        />
      </div>
    </label>
  );
}

function VendorCallButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label="Call Vendor"
      title="Call Vendor"
      onClick={onClick}
      className="btn-secondary inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-taupe transition hover:bg-ivory hover:text-bronze"
    >
      <Phone className="h-3.5 w-3.5" />
    </button>
  );
}

function Cell({
  row,
  column,
  categories,
  sections,
  onChange,
  onVendorCall,
}: {
  row: IntakeRow;
  column: string;
  categories: string[];
  sections: string[];
  onChange: (value: unknown) => void;
  onVendorCall: (missingFields: string[]) => void;
}) {
  if (column === "Include" || column === "Review Required") {
    return (
      <input
        type="checkbox"
        checked={Boolean(row[column])}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-bronze"
      />
    );
  }

  if (column === "Confidence Score") {
    const score = Number(row[column] ?? 0);
    return <span className="font-medium text-charcoal/70">{score}%</span>;
  }

  if (column === "Image Upload Status") {
    return <StatusBadge value={rowText(row, column) || "Missing Image"} />;
  }

  if (column === "Status") {
    const value = rowText(row, column) || "Needs Review";
    return (
      <div className="grid gap-1.5">
        <StatusBadge value={value} />
        <input
          className="input-surface h-9 w-40 rounded-xl px-2 text-sm"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    );
  }

  if (column === "Product Category") {
    return (
      <select
        className="input-surface h-9 w-36 rounded-xl px-2 text-sm"
        value={rowText(row, column)}
        onChange={(event) => onChange(event.target.value)}
      >
        {sections.map((category) => (
          <option key={category} value={category}>
            {category}
          </option>
        ))}
      </select>
    );
  }

  if (column === "Suggested Action" || column === "Notes") {
    const value = column === "Notes" ? cleanNotes(rowText(row, column)) : rowText(row, column);
    return (
      <textarea
        className="input-surface min-h-9 w-56 resize-none rounded-xl px-2 py-2 text-sm"
        value={value}
        onChange={(event) => onChange(column === "Notes" ? cleanNotes(event.target.value) : event.target.value)}
      />
    );
  }

  const disabled = column === "Project";
  const width = column === "Product Name" ? "w-56" : "w-40";
  return (
    <input
      className={`input-surface ${width} h-9 rounded-xl px-2 text-sm disabled:bg-ivory disabled:text-charcoal/50`}
      value={rowText(row, column)}
      disabled={disabled}
      onChange={(event) => onChange(column === "Quantity" ? Number(event.target.value) : event.target.value)}
    />
  );
}
