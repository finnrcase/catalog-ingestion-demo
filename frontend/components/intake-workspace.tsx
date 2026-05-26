"use client";

import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Copy,
  Download,
  FileText,
  ImageIcon,
  Loader2,
  Phone,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  exportProgramaCsv,
  exportProgramaXlsx,
  exportProgramaXlsxWithImages,
  exportProgramaZip,
  cancelPdfParseJob,
  fetchHealth,
  fetchPdfParseJob,
  fetchPdfParseLogs,
  fetchSchema,
  fetchVendorCallStatus,
  enrichRows,
  generateIntakeTable,
  generateVendorCallScript,
  recoverImages,
  refreshVendorCall,
  retryPdfParseJob,
  startVendorCall,
  uploadPdfForParsing,
  uploadImage,
  validateProgramaExport,
  validateRows,
} from "@/lib/api";
import { hasComplete3dDimensions } from "@/lib/dimensions";
import type { IntakeResponse, IntakeRow, PdfParseJob, PhotoDiscoveryReport } from "@/lib/types";

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
  const enrichmentMetricsEntry = diagnostics.find((entry) => entry.report_type === "enrichment_metrics");
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
        matchedUrl: rowText(row, "Product URL") || null,
        sourceDomainsTried: rowText(row, "Source Domains Tried"),
        selectedDomain: rowText(row, "Selected Source Domain"),
        sourceSelectionReason: rowText(row, "Source Selection Reason"),
        dimensionsExtractionMethod: rowText(row, "Dimensions Extraction Method"),
        imageExtractionMethod: rowText(row, "Image Extraction Method"),
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
      `Remaining Budget: ${formatUsd(metrics.remaining_budget_usd)}`,
      `Search Calls: ${metrics.search_calls ?? 0}`,
      `Page Fetches: ${metrics.page_fetches ?? 0}`,
      `External Lookups: ${metrics.external_lookups ?? 0}/${metrics.external_lookups_limit ?? "?"}`,
      `Image Searches: ${metrics.image_searches ?? 0}/${metrics.image_searches_limit ?? "?"}`,
      `AI Calls: ${metrics.ai_calls ?? 0}`,
      `AI Call Limit: ${metrics.ai_calls_limit ?? "?"}`,
      `AI Calls Avoided: ${metrics.ai_calls_avoided ?? 0}`,
      `Cache Hit Rate: ${metrics.cache_hit_rate ?? 0}`,
      `Cache Hits: ${metrics.cache_hits ?? 0}`,
      `Duplicate Reuse: ${metrics.duplicate_reuse ?? 0}`,
      `Cheap Local Only: ${metrics.cheap_local_only ?? 0}`,
      `Skipped Enrichments: ${metrics.skipped_enrichments ?? 0}`,
      `Budget-Skipped Calls: ${metrics.skipped_calls_due_budget ?? 0}`,
      `Budget-Skipped Fields: ${metrics.fields_skipped_due_budget ?? 0}`,
      `Most Expensive Item: ${metrics.most_expensive_item || "none"} (${formatUsd(metrics.most_expensive_item_cost_usd)})`,
      `Cost By Stage: ${JSON.stringify(metrics.cost_by_stage || {})}`,
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
      `- Successful Source Stored: ${product.enrichment.successfulSourceStored || "none"}`,
      `- Rejected URLs: ${product.enrichment.rejectedUrlsAndReasons || "none"}`,
      `- Failed Fields: ${product.enrichment.failedFields.join(", ") || "none"}`,
      `- Failure Reason: ${product.enrichment.failureReason || "none"}`,
      `- Retry Count: ${product.enrichment.retryCount}`,
      `- Final Status: ${product.finalStatus}`,
      `- Confidence: ${product.confidenceScore}`,
      `- Confidence Reason: ${product.confidenceReasons || "none"}`,
      "Image Trace:",
      `- Query Used: ${product.imageTrace.queryUsed || "none"}`,
      `- Selected: ${product.imageTrace.selectedCandidate || "none"}`,
      `- Source: ${product.imageTrace.sourceType || "none"}`,
      `- Confidence: ${product.imageTrace.finalConfidence || "none"}`,
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
  const [showInternalDebug, setShowInternalDebug] = useState(false);
  const [debugCopyStatus, setDebugCopyStatus] = useState("");
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
  const [productImageUploads, setProductImageUploads] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<"generate" | "validate" | "vendorCall" | "export" | "photoBulk" | "imageRecovery" | "">("");
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
  const pdfSessionIdRef = useRef<string>("");

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
  const productListNeedsReview = Math.max(0, includedRows.length - readyRows);
  const needsReview = useMemo(
    () => includedRows.filter((row) => row["Review Required"] === true).length,
    [includedRows],
  );
  const internalDebugReport = useMemo(
    () => buildInternalDebugReport(rows, debugUploads, errors, exportSummary, latestDiagnostics),
    [rows, debugUploads, errors, exportSummary, latestDiagnostics],
  );
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
    if (["standard", "balanced", "deep", "manual_retry"].includes(enrichmentMode) && !INTERNAL_DEBUG_ENABLED) {
      setMessage("Balanced and deep enrichment are internal/admin-only.");
      return;
    }
    if (["deep", "manual_retry"].includes(enrichmentMode)) {
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
        enrichmentMode,
      });
      setRows(response.rows);
      setErrors(response.errors);
      setLatestDiagnostics(response.dimension_diagnostics || []);
      setPhotoDiscoveryReport(photoReportFromDiagnostics(response.dimension_diagnostics));
      setMessage(useWebEnrichment ? "Missing info search complete." : "Input updates saved without web search.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save input updates.");
    } finally {
      setBusy("");
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

  async function handleProgramaExport(format: "csv" | "xlsx" | "xlsx-images" | "zip") {
    setBusy("export");
    try {
      const blob =
        format === "zip"
          ? await exportProgramaZip(includedRows, includeLowConfidenceImages)
          : format === "xlsx-images"
          ? await exportProgramaXlsxWithImages(includedRows)
          : format === "xlsx"
          ? await exportProgramaXlsx(includedRows)
          : await exportProgramaCsv(includedRows);
      const today = new Date().toISOString().slice(0, 10);
      const filename =
        format === "zip"
          ? `programa_export_${today}.zip`
          : format === "xlsx-images"
          ? `programa_import_with_images_${today}.xlsx`
          : `programa_import_${today}.${format}`;
      downloadBlob(blob, filename);
      setMessage("Use this file for Programa Import Products.");
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
          <div className="flex flex-wrap gap-2">
            <StatusBadge value={`${readyRows} Ready`} />
            <StatusBadge value={`${needsReview} Needs Review`} />
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
              <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-dashed border-linen bg-white/70 px-4 py-4 transition hover:border-orangeBorder hover:bg-orangeSoft/40">
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
                  isImageDragActive ? "border-orangeBorder bg-orangeSoft" : "border-linen bg-white/70 hover:border-orangeBorder hover:bg-orangeSoft/40"
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
                  <div className="mt-2 grid gap-1 rounded-lg border border-clay/20 bg-white/70 p-2 text-xs text-charcoal">
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
                    className="rounded-full border border-linen bg-white px-3 py-1 hover:border-orangeBorder"
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
                <div className="overflow-x-auto rounded-xl border border-linen bg-white">
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
            <div className="rounded-xl border border-dashed border-linen bg-white/60 px-5 py-10 text-center">
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
              Use web search
            </label>

            <Field label="Enrichment mode">
              <select
                className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                value={enrichmentMode}
                onChange={(event) => setEnrichmentMode(event.target.value as typeof enrichmentMode)}
                disabled={!useWebEnrichment}
              >
                <option value="fast">Fast - cheapest</option>
                {INTERNAL_DEBUG_ENABLED ? <option value="standard">Balanced</option> : null}
                {INTERNAL_DEBUG_ENABLED ? <option value="deep">Deep enrichment</option> : null}
                {INTERNAL_DEBUG_ENABLED ? <option value="manual_retry">Manual retry</option> : null}
              </select>
            </Field>

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
                <div className="divide-y divide-linen rounded-xl border border-linen bg-white">
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
              disabled={busy === "validate" || rows.length === 0}
              onClick={handleValidate}
            >
              {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              Run Enrichment
            </button>
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
                        <div key={`${failed.model_sku}-${failedIndex}`} className="rounded-lg border border-linen bg-white p-2 text-xs text-taupe">
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
                    <div className="divide-y divide-linen rounded-xl border border-linen bg-white">
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
                        const imageDebugAvailable = Boolean(imageQueryUsed || imageCandidates.length || imageRejectedCandidates || rowText(row, "_selected_image_candidate"));
                        const callFields = phoneCallFieldsForRow(row);
                        const emphasizeCall = shouldEmphasizePhoneCall(row);
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
                                  <span className="inline-flex max-w-full items-center gap-2 align-middle">
                                    <img src={imageUrl} alt={productName || "Product image"} className="h-8 w-8 rounded-lg object-cover" />
                                    <span className="truncate text-charcoal">{imageUrl}</span>
                                  </span>
                                ) : (
                                  <span className="font-semibold text-clay">Missing</span>
                                )}
                              </div>
                              {uploadStatus ? (
                                <div className={`sm:col-span-2 ${uploadStatus === "Uploading..." ? "text-bronze" : "text-clay"}`}>
                                  {uploadStatus}
                                </div>
                              ) : null}
                              {evidence ? <div className="sm:col-span-2 truncate">Evidence: {evidence}</div> : null}
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
                              {rawGroupedText || parsedFields || enrichmentQuery || confidenceReason || missingInitial || imageDebugAvailable ? (
                                <details className="sm:col-span-2 rounded-lg border border-linen bg-ivory/40 p-2">
                                  <summary className="cursor-pointer text-xs font-semibold text-charcoal">Item debug</summary>
                                  <div className="mt-2 grid gap-1 text-xs text-taupe">
                                    {rawGroupedText ? (
                                      <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-white p-2 text-charcoal">{rawGroupedText}</pre>
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
        {INTERNAL_DEBUG_ENABLED ? (
          <InternalDebugPanel
            report={internalDebugReport}
            expanded={showInternalDebug}
            copyStatus={debugCopyStatus}
            onToggle={() => setShowInternalDebug((current) => !current)}
            onCopy={handleCopyInternalDebug}
            onDownload={handleDownloadInternalDebug}
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
      </div>
    </main>
  );
}

function StatusBadge({ value }: { value: string }) {
  const normal = value.toLowerCase();
  const tone =
    normal.startsWith("0 failed")
      ? "border-linen bg-white/70 text-taupe"
      : normal.includes("ready") || normal.includes("uploaded")
      ? "border-sage/20 bg-sage/10 text-sage"
      : normal.includes("missing") || normal.includes("failed")
        ? "border-clay/20 bg-clay/10 text-clay"
        : normal.includes("review")
          ? "border-orangeBorder bg-orangeSoft text-bronze"
          : "border-linen bg-white/70 text-taupe";
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
    <div className="rounded-xl border border-linen bg-white/70">
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
    <section className="rounded-2xl border border-linen bg-white/60 p-4 text-sm text-charcoal">
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
                <DebugLine label="Remaining" value={formatUsd(report.enrichmentMetrics.remaining_budget_usd)} />
                <DebugLine label="Search calls" value={report.enrichmentMetrics.search_calls ?? 0} />
                <DebugLine label="Page fetches" value={report.enrichmentMetrics.page_fetches ?? 0} />
                <DebugLine label="External lookups" value={`${report.enrichmentMetrics.external_lookups ?? 0}/${report.enrichmentMetrics.external_lookups_limit ?? "?"}`} />
                <DebugLine label="Image searches" value={`${report.enrichmentMetrics.image_searches ?? 0}/${report.enrichmentMetrics.image_searches_limit ?? "?"}`} />
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
            <details className="rounded-xl border border-linen bg-white/70 p-3 text-xs">
              <summary className="cursor-pointer font-semibold">Cost-control trace</summary>
              <div className="mt-3 grid gap-2">
                <DebugLine label="Cost by stage" value={JSON.stringify(report.enrichmentMetrics.cost_by_stage || {})} />
                <DebugLine label="Paid call reasons" value={JSON.stringify(report.enrichmentMetrics.paid_call_reasons || [])} />
                <DebugLine label="Budget skipped calls" value={JSON.stringify(report.enrichmentMetrics.budget_skipped_calls || [])} />
                <DebugLine label="Budget skipped fields" value={JSON.stringify(report.enrichmentMetrics.budget_skipped_fields || [])} />
              </div>
            </details>
          ) : null}

          <div className="grid gap-3">
            {report.products.length ? report.products.map((product) => (
              <details key={`${product.index}-${product.id}`} className="rounded-xl border border-linen bg-white/70 p-3">
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
                    <DebugLine label="Matched URL" value={product.enrichment.matchedUrl || "none"} />
                    <DebugLine label="Source domains" value={product.enrichment.sourceDomainsTried || "none"} />
                    <DebugLine label="Selected domain" value={product.enrichment.selectedDomain || "none"} />
                    <DebugLine label="Selection reason" value={product.enrichment.sourceSelectionReason || "none"} />
                    <DebugLine label="Dimensions method" value={product.enrichment.dimensionsExtractionMethod || "none"} />
                    <DebugLine label="Image method" value={product.enrichment.imageExtractionMethod || "none"} />
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
      className={`rounded-2xl border bg-white/72 p-5 transition-colors sm:p-6 ${
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-charcoal/25 px-4 py-6">
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
              className="btn-secondary inline-flex h-10 items-center justify-center rounded-xl px-4 text-sm font-semibold text-taupe hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/65"
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
                className="mt-3 inline-flex h-8 items-center justify-center rounded-lg border border-linen bg-white px-3 text-xs font-semibold text-taupe hover:bg-ivory disabled:cursor-not-allowed disabled:text-taupe/60"
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
