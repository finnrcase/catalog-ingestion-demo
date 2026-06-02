"use client";

import {
  Archive,
  CheckCircle2,
  ChevronDown,
  Download,
  FileText,
  ImageIcon,
  Loader2,
  Phone,
  Settings,
  Upload,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  ApiRequestError,
  exportProgramaCsv,
  exportProgramaDebugCsv,
  exportProgramaXlsx,
  exportProgramaZip,
  formatApiError,
  API_BASE,
  RAW_API_BASE,
  deletePreferredDomain,
  deleteStoredSource,
  fetchProductKnowledgeBaseAudit,
  fetchHealth,
  fetchIntegrationsStatus,
  fetchPreferredDomains,
  fetchSchema,
  fetchStoredSources,
  fetchVendorCallStatus,
  enrichRows,
  furtherEnrichRows,
  generateIntakeTable,
  generateVendorCallScript,
  refreshVendorCall,
  recoverMissingImages,
  reverifyStoredSource,
  sendToPrograma,
  savePreferredDomain,
  saveStoredSource,
  startVendorCall,
  updatePreferredDomain,
  updateStoredSource,
  uploadImage,
  validateProgramaExport,
  validateRows,
} from "@/lib/api";
import { hasComplete3dDimensions } from "@/lib/dimensions";
import type {
  IntakeResponse,
  IntakeRow,
  IntegrationsStatus,
  PreferredSourceDomain,
  ProgramaExportValidation,
  StoredProductSource,
} from "@/lib/types";

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

type BuildInfo = {
  commit: string;
  builtAt: string;
  version: string;
  repo: string;
  branch: string;
  environment: string;
  project: string;
  rootDirectory: string;
  homepageRoute: string;
  settingsRoute: string;
  workflowComponent: string;
  deploymentUrl?: string;
};

type WorkflowStage = "upload" | "parse" | "reviewParsed" | "enrich" | "reviewEnriched" | "export";
type EnrichmentMode = "fast" | "standard" | "deep";
type ThemePreference = "system" | "dark" | "light";
type AccentThemeId =
  | "orange"
  | "sage"
  | "blue"
  | "plum"
  | "mustard"
  | "terracotta"
  | "slateBlue"
  | "sand"
  | "forest"
  | "ocean"
  | "clay"
  | "rosewood";

type DebugTrace = {
  timestamp: string;
  route: string;
  action: string;
  stage: string;
  endpoint?: string;
  statusCode?: number;
  message?: string;
  itemId?: string;
  lastSuccessfulStage?: string;
  fallback?: string;
};

type AccentTheme = {
  id: AccentThemeId;
  label: string;
  accent: string;
  hover: string;
  soft: string;
  ring: string;
  foreground: string;
  text: string;
};

type UiMode = "explanation" | "simple";

type SourceTypeFilter = "" | "manufacturer" | "retailer" | "spec_sheet" | "manual" | "other";

type SourceForm = {
  brand: string;
  model_sku: string;
  product_name: string;
  product_page_url: string;
  manufacturer_url: string;
  spec_sheet_url: string;
  image_url: string;
  dimension_source_url: string;
  dimensions: string;
  width_in: string;
  height_in: string;
  depth_in: string;
  source_type: SourceTypeFilter;
  notes: string;
};

type DomainForm = {
  domain: string;
  source_type: SourceTypeFilter;
  notes: string;
};

const DEBUG_MODE_STORAGE_KEY = "sch-intake-debug-mode";
const THEME_STORAGE_KEY = "sch-intake-theme";
const ACCENT_THEME_STORAGE_KEY = "sch-intake-accent-theme";
const UI_MODE_STORAGE_KEY = "sch-intake-ui-mode";
const sourceTypeOptions: { value: SourceTypeFilter; label: string }[] = [
  { value: "", label: "All source types" },
  { value: "manufacturer", label: "Manufacturer" },
  { value: "retailer", label: "Trusted retailer" },
  { value: "spec_sheet", label: "Manual / spec archive" },
  { value: "manual", label: "Manual / guide" },
  { value: "other", label: "Other" },
];
const emptySourceForm: SourceForm = {
  brand: "",
  model_sku: "",
  product_name: "",
  product_page_url: "",
  manufacturer_url: "",
  spec_sheet_url: "",
  image_url: "",
  dimension_source_url: "",
  dimensions: "",
  width_in: "",
  height_in: "",
  depth_in: "",
  source_type: "manufacturer",
  notes: "",
};
const emptyDomainForm: DomainForm = {
  domain: "",
  source_type: "manufacturer",
  notes: "",
};
const ENRICHMENT_BUDGET_STORAGE_KEY = "sch-intake-enrichment-budget-usd";
const DEFAULT_ENRICHMENT_BUDGET_USD = 0.25;
const FURTHER_ENRICHMENT_ENABLED_STORAGE_KEY = "sch-intake-further-enrichment-enabled";
const FURTHER_ENRICHMENT_BUDGET_STORAGE_KEY = "sch-intake-further-enrichment-budget-usd";
const DEFAULT_FURTHER_ENRICHMENT_BUDGET_USD = 0.25;

const themeOptions: { id: ThemePreference; label: string; description: string }[] = [
  { id: "light", label: "Light", description: "A warm, clean light workspace." },
  { id: "dark", label: "Dark", description: "The current SCH dark workspace." },
  { id: "system", label: "System", description: "Follow your browser or OS preference." },
];

const accentThemes: AccentTheme[] = [
  {
    id: "orange",
    label: "Orange",
    accent: "#f97316",
    hover: "#ea580c",
    soft: "#2c2118",
    ring: "#fdba74",
    foreground: "#ffffff",
    text: "#fb923c",
  },
  {
    id: "sage",
    label: "Sage",
    accent: "#5f7a65",
    hover: "#4d6653",
    soft: "#1f2a22",
    ring: "#a8b9ad",
    foreground: "#ffffff",
    text: "#a8b9ad",
  },
  {
    id: "blue",
    label: "Blue",
    accent: "#3f6f8f",
    hover: "#315b75",
    soft: "#1b2730",
    ring: "#9ebbd0",
    foreground: "#ffffff",
    text: "#9ebbd0",
  },
  {
    id: "plum",
    label: "Plum",
    accent: "#7d5266",
    hover: "#684354",
    soft: "#2a2026",
    ring: "#c9aabb",
    foreground: "#ffffff",
    text: "#c9aabb",
  },
  {
    id: "mustard",
    label: "Mustard",
    accent: "#a87a22",
    hover: "#8c651b",
    soft: "#2b2517",
    ring: "#d9bd72",
    foreground: "#ffffff",
    text: "#d9bd72",
  },
  {
    id: "terracotta",
    label: "Terracotta",
    accent: "#a65f43",
    hover: "#8c4f38",
    soft: "#2d211c",
    ring: "#d2a08c",
    foreground: "#ffffff",
    text: "#d2a08c",
  },
  {
    id: "slateBlue",
    label: "Slate Blue",
    accent: "#56657f",
    hover: "#46536a",
    soft: "#202632",
    ring: "#a8b1c0",
    foreground: "#ffffff",
    text: "#a8b1c0",
  },
  {
    id: "sand",
    label: "Sand",
    accent: "#967f5c",
    hover: "#7d6a4c",
    soft: "#29251d",
    ring: "#cab892",
    foreground: "#ffffff",
    text: "#cab892",
  },
  {
    id: "forest",
    label: "Forest",
    accent: "#3f604b",
    hover: "#344f3e",
    soft: "#1c2920",
    ring: "#94ad9d",
    foreground: "#ffffff",
    text: "#94ad9d",
  },
  {
    id: "ocean",
    label: "Ocean",
    accent: "#2f7780",
    hover: "#28636a",
    soft: "#192b2e",
    ring: "#91c0c5",
    foreground: "#ffffff",
    text: "#91c0c5",
  },
  {
    id: "clay",
    label: "Clay",
    accent: "#9a5a48",
    hover: "#824c3d",
    soft: "#2c211e",
    ring: "#c89b8e",
    foreground: "#ffffff",
    text: "#c89b8e",
  },
  {
    id: "rosewood",
    label: "Rosewood",
    accent: "#854f55",
    hover: "#704248",
    soft: "#2a2022",
    ring: "#c79da3",
    foreground: "#ffffff",
    text: "#c79da3",
  },
];

const frontendRouteWiring = [
  {
    action: "Backend health",
    endpoint: "GET /health",
    body: "none",
    response: "{ status }",
  },
  {
    action: "Schema/categories",
    endpoint: "GET /schema",
    body: "none",
    response: "{ categories, sections, statuses, reviewFields }",
  },
  {
    action: "Integrations status",
    endpoint: "GET /integrations/status",
    body: "none",
    response: "{ openai: { status, configured, model }, further_enrichment }",
  },
  {
    action: "PDF parse / product links",
    endpoint: "POST /intake/generate",
    body: "multipart FormData: project, room, urls, use_ai_pdf, files[]",
    response: "IntakeResponse: rows, errors, eligible_count, blocked_count, stage_timings",
  },
  {
    action: "Bulk/product image upload",
    endpoint: "POST /api/upload-image",
    body: "multipart FormData: file",
    response: "{ secure_url, stage_timings }",
  },
  {
    action: "Row validation",
    endpoint: "POST /intake/validate",
    body: "{ rows }",
    response: "IntakeResponse",
  },
  {
    action: "Enrich missing data",
    endpoint: "POST /intake/enrich",
    body: "{ rows, use_web_enrichment, enrichment_mode, force_refresh, enrichment_budget_usd }",
    response: "IntakeResponse + dimension_diagnostics/stage_timings",
  },
  {
    action: "Targeted missing image recovery",
    endpoint: "POST /intake/recover-images",
    body: "{ rows, enrichment_mode, force_refresh }",
    response: "IntakeResponse + image diagnostics in dimension_diagnostics",
  },
  {
    action: "Further enrichment",
    endpoint: "POST /intake/further-enrich",
    body: "{ rows, further_enrichment_enabled, further_enrichment_budget_usd }",
    response: "IntakeResponse + further_enrichment stage_timings",
  },
  {
    action: "Export validation",
    endpoint: "POST /export/programa/validate",
    body: "{ rows }",
    response: "ProgramaExportValidation",
  },
  {
    action: "Excel export",
    endpoint: "POST /export/programa/xlsx",
    body: "{ rows }",
    response: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  },
  {
    action: "CSV export",
    endpoint: "POST /export/programa/csv",
    body: "{ rows }",
    response: "text/csv",
  },
  {
    action: "ZIP with images",
    endpoint: "POST /export/programa/zip",
    body: "{ rows }",
    response: "application/zip",
  },
  {
    action: "Debug export",
    endpoint: "POST /export/programa/debug-csv",
    body: "{ rows }",
    response: "text/csv",
  },
  {
    action: "Send to Programa",
    endpoint: "POST /programa/send",
    body: "{ project_name, schedule_url, rows, allow_blank_fields, upload_product_images }",
    response: "{ status, message, entries, blocked }",
  },
  {
    action: "Vendor phone workflow",
    endpoint: "POST /vendor-call/script, POST /vendor-call/start, POST /vendor-call/refresh",
    body: "{ row, missing_fields, phone_number, custom_goal/call_id }",
    response: "script/status/transcript payloads",
  },
] as const;

function rowText(row: IntakeRow, key: string) {
  return String(row[key] ?? "");
}

function boolish(value: unknown) {
  if (typeof value === "boolean") return value;
  return ["true", "1", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function hasValue(row: IntakeRow, key: string) {
  return Boolean(rowText(row, key).trim());
}

function hasImage(row: IntakeRow) {
  return hasValue(row, "Image URL");
}

function hasSku(row: IntakeRow) {
  return hasValue(row, "Model/SKU");
}

function hasSupplier(row: IntakeRow) {
  return hasValue(row, "Supplier");
}

function isReviewOnlyCharge(row: IntakeRow) {
  const importType = rowText(row, "Import Type").trim().toLowerCase();
  return importType === "unresolved_charge" || importType === "manual_review_charge";
}

function isProductRow(row: IntakeRow) {
  return row.Include !== false && !isReviewOnlyCharge(row);
}

function countRows(rows: IntakeRow[], predicate: (row: IntakeRow) => boolean) {
  return rows.filter(predicate).length;
}

function getEstimatedCost(response?: { estimated_cost?: unknown; cost_estimate?: unknown; stage_timings?: Record<string, unknown> }) {
  const raw =
    response?.estimated_cost ??
    response?.cost_estimate ??
    response?.stage_timings?.estimated_cost ??
    response?.stage_timings?.cost_estimate ??
    response?.stage_timings?.estimated_cost_usd ??
    response?.stage_timings?.cost_usd;

  if (raw === undefined || raw === null || raw === "") return "Not reported";
  if (typeof raw === "number") return `$${raw.toFixed(raw < 1 ? 4 : 2)}`;
  return String(raw);
}

function stageNumber(response: { stage_timings?: Record<string, unknown> } | undefined, key: string) {
  const raw = response?.stage_timings?.[key];
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const parsed = Number.parseFloat(raw);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function formatUsd(value: number | undefined, digits = 2) {
  if (value === undefined || !Number.isFinite(value)) return "Not reported";
  return `$${value.toFixed(digits)}`;
}

function clampBudgetUsd(value: number) {
  if (!Number.isFinite(value)) return DEFAULT_ENRICHMENT_BUDGET_USD;
  return Math.min(5, Math.max(0, value));
}

function getAccentTheme(themeId: string | null | undefined) {
  return accentThemes.find((theme) => theme.id === themeId) || accentThemes[0];
}

function hexToRgbTriplet(hex: string) {
  const normalized = hex.replace("#", "");
  const value = normalized.length === 3
    ? normalized.split("").map((char) => char + char).join("")
    : normalized;
  const intValue = Number.parseInt(value, 16);
  if (!Number.isFinite(intValue)) return "249 115 22";
  return `${(intValue >> 16) & 255} ${(intValue >> 8) & 255} ${intValue & 255}`;
}

function isThemePreference(value: string | null): value is ThemePreference {
  return value === "system" || value === "dark" || value === "light";
}

function resolveThemePreference(preference: ThemePreference) {
  if (preference !== "system") return preference;
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyThemePreference(preference: ThemePreference) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  root.dataset.theme = resolveThemePreference(preference);
}

function applyAccentTheme(theme: AccentTheme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.style.setProperty("--accent", theme.accent);
  root.style.setProperty("--accent-rgb", hexToRgbTriplet(theme.accent));
  root.style.setProperty("--accent-hover", theme.hover);
  root.style.setProperty("--accent-hover-rgb", hexToRgbTriplet(theme.hover));
  root.style.setProperty("--accent-soft", theme.soft);
  root.style.setProperty("--accent-soft-rgb", hexToRgbTriplet(theme.soft));
  root.style.setProperty("--accent-ring", theme.ring);
  root.style.setProperty("--accent-ring-rgb", hexToRgbTriplet(theme.ring));
  root.style.setProperty("--accent-foreground", theme.foreground);
  root.style.setProperty("--accent-text", theme.text);
}

function safeEndpointPath(value: string | undefined) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return `${parsed.pathname}${parsed.search ? "?..." : ""}`;
  } catch {
    return value.replace(/^https?:\/\/[^/]+/i, "") || value;
  }
}

function sanitizedErrorMessage(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error ?? "Unknown error");
  return raw
    .replace(/(api[_-]?key|token|secret|password|authorization)=([^&\s]+)/gi, "$1=[redacted]")
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer [redacted]")
    .slice(0, 500);
}

function sanitizedDebugText(value: unknown, limit = 5000) {
  return String(value ?? "")
    .replace(/(api[_-]?key|token|secret|password|authorization)=([^&\s]+)/gi, "$1=[redacted]")
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer [redacted]")
    .slice(0, limit);
}

function debugDetailsFromError(error: unknown) {
  if (error instanceof ApiRequestError) {
    const responseText = sanitizedDebugText(error.details.responseText, 5000);
    return {
      endpoint: safeEndpointPath(error.details.endpoint),
      statusCode: error.details.status,
      message: responseText ? `${sanitizedErrorMessage(error)}\n${responseText}` : sanitizedErrorMessage(error),
    };
  }
  return { message: sanitizedErrorMessage(error) };
}

function isTechnicalErrorMessage(value: string) {
  return /\b(invalid ipv6 url|traceback|stack|httpx|urllib|cors|preflight|networkerror|failed to fetch)\b/i.test(value);
}

function userFacingApiMessage(error: unknown, debugMode: boolean) {
  if (debugMode) return formatApiError(error);
  if (error instanceof ApiRequestError) {
    if (error.details.kind === "config") {
      return "Backend is not configured. Open Settings to check the API connection.";
    }
    if (error.details.status && error.details.status >= 500) {
      return "The backend failed before completing this step. Turn on Debug Mode for technical details.";
    }
    return "Some enrichment sources failed. Retry or turn on Debug Mode for technical details.";
  }
  const message = error instanceof Error ? error.message : String(error ?? "Request failed.");
  return isTechnicalErrorMessage(message)
    ? "Some enrichment sources failed. Retry or turn on Debug Mode for technical details."
    : message;
}

function displayErrorsForMode(errors: string[], debugMode: boolean) {
  if (debugMode) return Array.from(new Set(errors));
  const technicalCount = errors.filter(isTechnicalErrorMessage).length;
  const userErrors = errors.filter((error) => !isTechnicalErrorMessage(error));
  const shown = technicalCount
    ? ["Some enrichment sources failed. Retry or turn on Debug Mode for technical details.", ...userErrors]
    : userErrors;
  return Array.from(new Set(shown));
}

function normalizeReportToken(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function reportDomain(value: string) {
  if (!value) return "";
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function parseReportCandidates(value: string) {
  if (!value.trim()) return [] as string[];
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) return [value];
    return parsed
      .map((candidate) => {
        if (typeof candidate === "string") return candidate;
        if (candidate && typeof candidate === "object") {
          const record = candidate as Record<string, unknown>;
          return [record.url, record.title, record.rejection_reason || record.confidence]
            .filter(Boolean)
            .join(" | ");
        }
        return "";
      })
      .filter(Boolean)
      .slice(0, 5);
  } catch {
    return value.split(/\s*\|\s*/).filter(Boolean).slice(0, 5);
  }
}

function uniqueProductReportGroups(rows: IntakeRow[]) {
  const groups = new Map<
    string,
    {
      key: string;
      normalizedBrand: string;
      normalizedModel: string;
      rows: { row: IntakeRow; index: number }[];
    }
  >();

  rows.forEach((row, index) => {
    const brand = rowText(row, "Brand");
    const model = rowText(row, "Model/SKU");
    const normalizedBrand = normalizeReportToken(brand) || "missingbrand";
    const normalizedModel = normalizeReportToken(model) || `missingmodel${index + 1}`;
    const key = `${normalizedBrand}_${normalizedModel}`;
    const existing =
      groups.get(key) ||
      {
        key,
        normalizedBrand,
        normalizedModel,
        rows: [],
      };
    existing.rows.push({ row, index });
    groups.set(key, existing);
  });

  return Array.from(groups.values());
}

function reportLine(label: string, value: unknown) {
  const text = String(value ?? "").trim();
  return `${label}: ${text || "none"}`;
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

function cleanNotes(value: string) {
  return value.replace(/^\s*(?:row\s*)?#?\d{1,3}\s*[-–—:.)]\s+(?=[A-Za-z\[\("'])/i, "").trim();
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

function confidenceIsLow(row: IntakeRow) {
  const rawValues = [
    rowText(row, "Dimension Confidence"),
    rowText(row, "dimension_confidence"),
    rowText(row, "image_confidence"),
    rowText(row, "selected_product_url_confidence"),
    rowText(row, "product_url_confidence"),
  ].map((value) => value.trim().toLowerCase());
  if (rawValues.some((value) => value === "low" || value === "none")) return true;
  const score = Number(row["Confidence Score"] ?? 0);
  return Number.isFinite(score) && score > 0 && score < 0.75;
}

function needsFurtherEnrichment(row: IntakeRow) {
  if (!isProductRow(row)) return false;
  return !hasComplete3dDimensions(row.Dimensions) || !isPublicHttpsImageUrl(rowText(row, "Image URL")) || confidenceIsLow(row);
}

function estimateFurtherCost(rows: IntakeRow[], cap: number) {
  const candidates = rows.filter(needsFurtherEnrichment).length;
  if (!candidates) return 0;
  return Math.min(cap, Math.max(0.0025, 0.006 * candidates + 0.004));
}

function isColumnMissing(row: IntakeRow, column: string) {
  if (!callFieldColumns.includes(column)) return false;
  const label = callFieldLabels[column] || column;
  return missingFieldsForRow(row).includes(label);
}

function LogoMark() {
  return (
    <div className="flex items-center gap-4">
      <div className="min-w-[132px] rounded-2xl border border-orangeBorder bg-orangeSoft/80 px-4 py-3 text-center shadow-sm">
        <div className="font-serif text-[28px] font-light leading-none tracking-[0.24em] text-charcoal">
          SCH
        </div>
        <div className="mt-1 text-[9px] font-semibold uppercase leading-tight tracking-[0.22em] text-bronze">
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

const fallbackBuildInfo: BuildInfo = {
  commit: "local",
  builtAt: "local",
  version: "0.1.0",
  repo: "catalog-ingestion-demo",
  branch: "local",
  environment: "local",
  project: "frontend",
  rootDirectory: "frontend",
  homepageRoute: "frontend/app/page.tsx",
  settingsRoute: "frontend/components/intake-workspace.tsx",
  workflowComponent: "frontend/components/intake-workspace.tsx",
  deploymentUrl: "",
};

function mainWorkflowStepIndex(stage: WorkflowStage) {
  if (stage === "parse" || stage === "reviewParsed") return 1;
  if (stage === "enrich" || stage === "reviewEnriched") return 2;
  if (stage === "export") return 3;
  return 0;
}

export function IntakeWorkspace({ buildInfo = fallbackBuildInfo }: { buildInfo?: BuildInfo }) {
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
  const [useAiPdf, setUseAiPdf] = useState(true);
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [sections, setSections] = useState<string[]>(fallbackSections);
  const [message, setMessage] = useState("");
  const [useWebEnrichment, setUseWebEnrichment] = useState(true);
  const [enrichmentMode, setEnrichmentMode] = useState<EnrichmentMode>("fast");
  const [forceRefreshEnrichment, setForceRefreshEnrichment] = useState(false);
  const [enrichmentBudgetUsd, setEnrichmentBudgetUsd] = useState(DEFAULT_ENRICHMENT_BUDGET_USD);
  const [furtherEnrichmentEnabled, setFurtherEnrichmentEnabled] = useState(false);
  const [furtherEnrichmentBudgetUsd, setFurtherEnrichmentBudgetUsd] = useState(DEFAULT_FURTHER_ENRICHMENT_BUDGET_USD);
  const [debugMode, setDebugMode] = useState(false);
  const [themePreference, setThemePreference] = useState<ThemePreference>("dark");
  const [accentThemeId, setAccentThemeId] = useState<AccentThemeId>("orange");
  const [uiMode, setUiMode] = useState<UiMode>("explanation");
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const [debugTraces, setDebugTraces] = useState<DebugTrace[]>([]);
  const [debugCopyStatus, setDebugCopyStatus] = useState("");
  const [enrichmentDebugReport, setEnrichmentDebugReport] = useState("");
  const [enrichmentDebugReportStatus, setEnrichmentDebugReportStatus] = useState("");
  const [lastSuccessfulStage, setLastSuccessfulStage] = useState("App loaded");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sourceMemoryStatus, setSourceMemoryStatus] = useState("Not loaded");
  const [sourceMemoryBackend, setSourceMemoryBackend] = useState("unknown");
  const [sourceMemoryAudit, setSourceMemoryAudit] = useState<{
    runtime_cache_persistent?: boolean;
    runtime_cache_persistence_note?: string;
    product_enrichment_cache_path?: string;
    product_knowledge_base_backend?: string;
    tables?: string[];
  }>({});
  const [storedSources, setStoredSources] = useState<StoredProductSource[]>([]);
  const [preferredDomains, setPreferredDomains] = useState<PreferredSourceDomain[]>([]);
  const [sourceSearch, setSourceSearch] = useState("");
  const [domainSearch, setDomainSearch] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceTypeFilter>("");
  const [domainTypeFilter, setDomainTypeFilter] = useState<SourceTypeFilter>("");
  const [sourceForm, setSourceForm] = useState<SourceForm>(emptySourceForm);
  const [domainForm, setDomainForm] = useState<DomainForm>(emptyDomainForm);
  const [editingSourceId, setEditingSourceId] = useState("");
  const [editingDomainId, setEditingDomainId] = useState("");
  const [workflowStage, setWorkflowStage] = useState<WorkflowStage>("upload");
  const [parseReviewed, setParseReviewed] = useState(false);
  const [enrichmentReviewed, setEnrichmentReviewed] = useState(false);
  const [exportGenerated, setExportGenerated] = useState(false);
  const [parsedProductsOpen, setParsedProductsOpen] = useState(false);
  const [enrichedProductsOpen, setEnrichedProductsOpen] = useState(false);
  const [parseStatus, setParseStatus] = useState("Ready for upload.");
  const [enrichmentStatus, setEnrichmentStatus] = useState("Not started.");
  const [apiStatus, setApiStatus] = useState<"checking" | "online" | "offline">("checking");
  const [apiStatusText, setApiStatusText] = useState("Checking backend...");
  const [integrationsStatus, setIntegrationsStatus] = useState<IntegrationsStatus>({
    openai: { status: "Not Configured", configured: false, model: "Not reported" },
  });
  const [lastEndpoint, setLastEndpoint] = useState("");
  const [estimatedCost, setEstimatedCost] = useState("Not reported");
  const [enrichmentStats, setEnrichmentStats] = useState({
    filledImages: 0,
    filledDimensions: 0,
    unresolved: 0,
    rowsEnriched: 0,
    missingDimensions: 0,
    missingImages: 0,
    averageConfidence: "Not reported",
    cacheHits: 0,
    duplicateModelsSkipped: 0,
    searchesAvoided: 0,
    usefulFieldsFound: 0,
    costPerUsefulField: "Not reported",
    costPerDimensionFound: "Not reported",
    budgetCap: formatUsd(DEFAULT_ENRICHMENT_BUDGET_USD),
    uniqueProductsSearched: 0,
    dimensionsFoundPerDollar: "Not reported",
    imagesFoundPerDollar: "Not reported",
  });
  const [scheduleUrl, setScheduleUrl] = useState("");
  const [programaMessage, setProgramaMessage] = useState("");
  const [productImageUploads, setProductImageUploads] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<"generate" | "validate" | "furtherEnrichment" | "imageRecovery" | "vendorCall" | "export" | "photoBulk" | "programa" | "">("");
  const [exportSummary, setExportSummary] = useState<ProgramaExportValidation>({
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
    duplicates_removed: [] as { index: number; product_name: string; brand?: string; sku?: string; kept_index?: number; reason?: string }[],
    duplicate_rows_removed: 0,
    suspicious_dimensions_rejected: [] as { index: number; product_name: string; brand?: string; sku?: string; dimensions?: string; reason?: string }[],
    rejected_product_urls: [] as { index: number; product_name: string; brand?: string; sku?: string; url?: string; reason?: string }[],
    pdf_product_urls: [] as { index: number; product_name: string; brand?: string; sku?: string; url?: string; reason?: string }[],
    blank_price_only_rows: [] as { index: number; price?: string; reason?: string }[],
    missing_model_manufacturer: [] as { index: number; product_name: string; brand?: string; sku?: string; reason?: string }[],
    phone_email_header_contamination: [] as { index: number; product_name: string; brand?: string; sku?: string; reason?: string }[],
    parsed_rows_count: 0,
    export_rows_count: 0,
    readiness_score: 0,
    readiness_status: "not_programa_ready",
    readiness_missing_fields: {} as Record<string, number>,
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

  useEffect(() => {
    setApiStatus("checking");
    fetchHealth()
      .then((health) => {
        setApiStatus("online");
        setApiStatusText(`Connected${health.status ? `: ${health.status}` : ""}`);
        setLastSuccessfulStage("Backend health check");
        recordDebugTrace({
          action: "backend health",
          stage: "success",
          endpoint: "/health",
          message: health.status || "ok",
        });
      })
      .catch((error) => {
        setApiStatus("offline");
        const friendlyMessage = "Backend is offline or not configured. Open Settings to check the API connection.";
        setApiStatusText(friendlyMessage);
        setMessage(friendlyMessage);
        recordDebugTrace({
          action: "backend health",
          stage: "failed",
          endpoint: "/health",
          ...debugDetailsFromError(error),
        });
      });
    fetchSchema()
      .then((schema) => {
        setCategories(schema.categories);
        setSections(schema.sections?.length ? schema.sections : fallbackSections);
        setPhotoBulkSection(schema.sections?.includes("Decor") ? "Decor" : schema.sections?.[0] || "General");
        setLastSuccessfulStage("Schema loaded");
        recordDebugTrace({
          action: "load schema",
          stage: "success",
          endpoint: "/schema",
          message: `${schema.categories?.length || 0} categories`,
        });
      })
      .catch(() => {
        setCategories([]);
        setSections(fallbackSections);
        setPhotoBulkSection("Decor");
        recordDebugTrace({
          action: "load schema",
          stage: "fallback",
          endpoint: "/schema",
          message: "Using local fallback sections.",
          fallback: "local fallback sections",
        });
      });
    fetchIntegrationsStatus()
      .then((status) => {
        setIntegrationsStatus(status);
        recordDebugTrace({
          action: "load integrations status",
          stage: "success",
          endpoint: "/integrations/status",
          message: `OpenAI ${status.openai?.status || "Not Configured"} · model ${status.openai?.model || "not reported"}`,
        });
      })
      .catch((error) => {
        setIntegrationsStatus({
          openai: { status: "Not Configured", configured: false, model: "Not reported" },
        });
        recordDebugTrace({
          action: "load integrations status",
          stage: "failed",
          endpoint: "/integrations/status",
          ...debugDetailsFromError(error),
        });
      });
  }, []);

  useEffect(() => {
    try {
      const storedDebugMode = window.localStorage.getItem(DEBUG_MODE_STORAGE_KEY);
      const storedThemePreference = window.localStorage.getItem(THEME_STORAGE_KEY);
      const storedAccentTheme = window.localStorage.getItem(ACCENT_THEME_STORAGE_KEY);
      const storedUiMode = window.localStorage.getItem(UI_MODE_STORAGE_KEY);
      const storedBudget = Number.parseFloat(window.localStorage.getItem(ENRICHMENT_BUDGET_STORAGE_KEY) || "");
      const storedFurtherEnabled = window.localStorage.getItem(FURTHER_ENRICHMENT_ENABLED_STORAGE_KEY);
      const storedFurtherBudget = Number.parseFloat(window.localStorage.getItem(FURTHER_ENRICHMENT_BUDGET_STORAGE_KEY) || "");
      setDebugMode(storedDebugMode === "true");
      setThemePreference(isThemePreference(storedThemePreference) ? storedThemePreference : "dark");
      setAccentThemeId(getAccentTheme(storedAccentTheme).id);
      setUiMode(storedUiMode === "simple" ? "simple" : "explanation");
      setEnrichmentBudgetUsd(Number.isFinite(storedBudget) ? clampBudgetUsd(storedBudget) : DEFAULT_ENRICHMENT_BUDGET_USD);
      setFurtherEnrichmentEnabled(storedFurtherEnabled === "true");
      setFurtherEnrichmentBudgetUsd(Number.isFinite(storedFurtherBudget) ? clampBudgetUsd(storedFurtherBudget) : DEFAULT_FURTHER_ENRICHMENT_BUDGET_USD);
    } catch {
      setDebugMode(false);
      setThemePreference("dark");
      setAccentThemeId("orange");
      setUiMode("explanation");
      setEnrichmentBudgetUsd(DEFAULT_ENRICHMENT_BUDGET_USD);
      setFurtherEnrichmentEnabled(false);
      setFurtherEnrichmentBudgetUsd(DEFAULT_FURTHER_ENRICHMENT_BUDGET_USD);
    }
    setSettingsHydrated(true);
  }, []);

  useEffect(() => {
    if (!settingsHydrated) return;
    try {
      window.localStorage.setItem(ENRICHMENT_BUDGET_STORAGE_KEY, String(enrichmentBudgetUsd));
    } catch {
      // Local persistence is best-effort only.
    }
  }, [enrichmentBudgetUsd, settingsHydrated]);

  useEffect(() => {
    if (!settingsHydrated) return;
    try {
      window.localStorage.setItem(FURTHER_ENRICHMENT_ENABLED_STORAGE_KEY, String(furtherEnrichmentEnabled));
      window.localStorage.setItem(FURTHER_ENRICHMENT_BUDGET_STORAGE_KEY, String(furtherEnrichmentBudgetUsd));
    } catch {
      // Local persistence is best-effort only.
    }
  }, [furtherEnrichmentBudgetUsd, furtherEnrichmentEnabled, settingsHydrated]);

  useEffect(() => {
    if (!settingsHydrated) return;
    applyThemePreference(themePreference);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
    } catch {
      // Local persistence is best-effort only.
    }
    if (themePreference !== "system" || typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const handleSystemThemeChange = () => applyThemePreference("system");
    media.addEventListener?.("change", handleSystemThemeChange);
    return () => media.removeEventListener?.("change", handleSystemThemeChange);
  }, [themePreference, settingsHydrated]);

  useEffect(() => {
    if (!settingsHydrated) return;
    const theme = getAccentTheme(accentThemeId);
    applyAccentTheme(theme);
    try {
      window.localStorage.setItem(ACCENT_THEME_STORAGE_KEY, theme.id);
    } catch {
      // Local persistence is best-effort only.
    }
  }, [accentThemeId, settingsHydrated]);

  useEffect(() => {
    if (!settingsHydrated) return;
    try {
      window.localStorage.setItem(DEBUG_MODE_STORAGE_KEY, String(debugMode));
    } catch {
      // Local persistence is best-effort only.
    }
  }, [debugMode, settingsHydrated]);

  useEffect(() => {
    if (!settingsHydrated) return;
    try {
      window.localStorage.setItem(UI_MODE_STORAGE_KEY, uiMode);
    } catch {
      // Local persistence is best-effort only.
    }
  }, [uiMode, settingsHydrated]);

  useEffect(() => {
    if (!settingsOpen) return;
    void loadSourceMemory();
  }, [settingsOpen, sourceSearch, sourceTypeFilter, domainSearch, domainTypeFilter]);

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

  const includedRows = useMemo(() => rows.filter(isProductRow), [rows]);
  const readyRows = exportSummary.export_count;
  const missingInputRows = useMemo(
    () => includedRows.filter((row) => missingFieldsForRow(row).length > 0),
    [includedRows],
  );
  const needsReview = useMemo(
    () => includedRows.filter((row) => row["Review Required"] === true).length,
    [includedRows],
  );
  const ignored = useMemo(() => rows.filter((row) => row.Include === false || row.Status === "Ignored").length, [rows]);
  const missingSkuCount = useMemo(() => countRows(includedRows, (row) => !hasSku(row)), [includedRows]);
  const missingDimensionsCount = useMemo(
    () => countRows(includedRows, (row) => !hasComplete3dDimensions(row.Dimensions)),
    [includedRows],
  );
  const missingImageCount = useMemo(() => countRows(includedRows, (row) => !hasImage(row)), [includedRows]);
  const missingSupplierCount = useMemo(() => countRows(includedRows, (row) => !hasSupplier(row)), [includedRows]);
  const imagesFoundCount = useMemo(() => countRows(includedRows, hasImage), [includedRows]);
  const dimensionsFoundCount = useMemo(
    () => countRows(includedRows, (row) => hasComplete3dDimensions(row.Dimensions)),
    [includedRows],
  );
  const furtherEnrichmentCandidateCount = useMemo(
    () => countRows(includedRows, needsFurtherEnrichment),
    [includedRows],
  );
  const furtherEnrichmentEstimatedCost = useMemo(
    () => estimateFurtherCost(includedRows, furtherEnrichmentBudgetUsd),
    [includedRows, furtherEnrichmentBudgetUsd],
  );
  const rowsWithRoomCount = useMemo(() => countRows(includedRows, (row) => Boolean(rowText(row, "Room").trim())), [includedRows]);
  const rowsWithSourceUrlCount = useMemo(() => countRows(includedRows, (row) => Boolean(rowText(row, "Product URL").trim())), [includedRows]);
  const rowsWithBrandCount = useMemo(() => countRows(includedRows, (row) => Boolean(rowText(row, "Brand").trim())), [includedRows]);
  const unresolvedCount = missingInputRows.length;
  const linkInputCount = urls.split(/\r?\n/).filter((url) => url.trim()).length;
  const parseInputCount = files.length + bulkImages.length + linkInputCount;
  const programaSendEnabled = process.env.NEXT_PUBLIC_PROGRAMA_SEND_ENABLED === "true";
  const activeMainWorkflowIndex = mainWorkflowStepIndex(workflowStage);
  const hasParsedRows = includedRows.length > 0;
  const uploadComplete = parseInputCount > 0 && !uploadError && !bulkImageError;
  const parsedReviewReady = hasParsedRows;
  const enrichmentHasRun = workflowStage === "reviewEnriched" || workflowStage === "export" || enrichmentReviewed || exportGenerated;
  const stageCompletion = {
    upload: uploadComplete,
    parse: parseReviewed,
    enrich: enrichmentReviewed,
    export: exportGenerated,
  };
  const completedStageCount = Object.values(stageCompletion).filter(Boolean).length;
  const workflowProgress = completedStageCount * 25;
  const stageSummaries = [
    {
      label: "Upload",
      title: uploadComplete ? "Upload Complete" : "Upload Needed",
      detail: uploadComplete
        ? [
            files.length ? `${files.length} PDF${files.length === 1 ? "" : "s"} uploaded` : "",
            bulkImages.length ? `${bulkImages.length} photo${bulkImages.length === 1 ? "" : "s"} selected` : "",
            linkInputCount ? `${linkInputCount} link${linkInputCount === 1 ? "" : "s"} pasted` : "",
          ].filter(Boolean).join(" · ")
        : "Add at least one PDF, photo, or product link.",
      complete: stageCompletion.upload,
      unlocked: true,
    },
    {
      label: "Parse",
      title: parseReviewed ? "Parse Complete" : hasParsedRows ? "Review Parse Results" : "Parse Locked",
      detail: hasParsedRows ? `${includedRows.length} product${includedRows.length === 1 ? "" : "s"} detected` : "Parse files after upload.",
      complete: stageCompletion.parse,
      unlocked: uploadComplete || hasParsedRows,
    },
    {
      label: "Enrich",
      title: enrichmentReviewed ? "Enrichment Complete" : enrichmentHasRun ? "Review Enrichment" : "Enrich Locked",
      detail: enrichmentHasRun
        ? `${Math.max(0, includedRows.length - unresolvedCount)}/${includedRows.length} product${includedRows.length === 1 ? "" : "s"} enriched`
        : "Enrich after parse review.",
      complete: stageCompletion.enrich,
      unlocked: parseReviewed || enrichmentHasRun,
    },
    {
      label: "Export",
      title: exportGenerated ? "Export Complete" : enrichmentReviewed ? "Export Ready" : "Export Locked",
      detail: exportGenerated
        ? "Programa import generated"
        : enrichmentReviewed
          ? "Programa import validated"
          : "Export after enrichment review.",
      complete: stageCompletion.export,
      unlocked: enrichmentReviewed || exportGenerated,
    },
  ];
  const currentStageSummary = stageSummaries[activeMainWorkflowIndex];
  const readinessScore =
    typeof exportSummary.readiness_score === "number"
      ? exportSummary.readiness_score
      : includedRows.length
        ? Math.round((exportSummary.export_count / includedRows.length) * 100)
        : 0;
  const apiConnectionStatus = API_BASE ? apiStatus : "misconfigured";
  const apiConnectionText = API_BASE ? apiStatusText : "NEXT_PUBLIC_API_BASE_URL is missing or invalid.";
  const displayApiBase = API_BASE || "not configured";
  const isSimpleMode = uiMode === "simple";

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
        duplicates_removed: [],
        duplicate_rows_removed: 0,
        suspicious_dimensions_rejected: [],
        rejected_product_urls: [],
        pdf_product_urls: [],
        blank_price_only_rows: [],
        missing_model_manufacturer: [],
        phone_email_header_contamination: [],
        parsed_rows_count: 0,
        export_rows_count: 0,
        readiness_score: 0,
        readiness_status: "not_programa_ready",
        readiness_missing_fields: {},
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
            readiness_score: 0,
            readiness_status: "not_programa_ready",
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

  function goToMainStage(index: number) {
    const target = stageSummaries[index];
    if (!target?.unlocked) return;
    if (index === 0) {
      setWorkflowStage("upload");
    } else if (index === 1) {
      setWorkflowStage(hasParsedRows ? "reviewParsed" : "parse");
    } else if (index === 2) {
      setWorkflowStage(enrichmentHasRun ? "reviewEnriched" : "enrich");
    } else if (index === 3) {
      setWorkflowStage("export");
    }
  }

  function continueToParse() {
    if (!uploadComplete) return;
    setWorkflowStage("parse");
  }

  function continueToEnrich() {
    if (!hasParsedRows || errors.some((error) => error.toLowerCase().includes("parse failed"))) return;
    setParseReviewed(true);
    setWorkflowStage("enrich");
  }

  function continueToExport() {
    if (!enrichmentHasRun) return;
    setEnrichmentReviewed(true);
    setWorkflowStage("export");
  }

  function updateRow(index: number, key: string, value: unknown) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  }

  function recordDebugTrace(event: Omit<DebugTrace, "timestamp" | "route" | "lastSuccessfulStage">) {
    const trace: DebugTrace = {
      ...event,
      timestamp: new Date().toISOString(),
      route: typeof window !== "undefined" ? window.location.pathname : "/",
      endpoint: safeEndpointPath(event.endpoint),
      message: event.message ? sanitizedDebugText(event.message, 5000) : undefined,
      lastSuccessfulStage,
    };
    setDebugTraces((current) => [...current.slice(-39), trace]);
  }

  function buildDebugReport() {
    return {
      generatedAt: new Date().toISOString(),
      route: typeof window !== "undefined" ? window.location.pathname : "/",
      workflowStage,
      lastSuccessfulStage,
      api: {
        status: apiConnectionStatus,
        baseConfigured: Boolean(API_BASE),
        resolvedBase: displayApiBase,
        lastEndpoint: safeEndpointPath(lastEndpoint),
      },
      settings: {
        debugMode,
        accentTheme: accentThemeId,
        useAiPdf,
        useWebEnrichment,
        enrichmentMode,
        enrichmentBudgetUsd,
        forceRefreshEnrichment,
        furtherEnrichmentEnabled,
        furtherEnrichmentBudgetUsd,
      },
      counts: {
        rows: rows.length,
        includedRows: includedRows.length,
        missingSku: missingSkuCount,
        missingDimensions: missingDimensionsCount,
        missingImages: missingImageCount,
        exportReady: exportSummary.export_count,
      },
      sourceMemory: {
        backend: sourceMemoryBackend,
        status: sourceMemoryStatus,
        audit: sourceMemoryAudit,
        storedSources: storedSources.length,
        preferredDomains: preferredDomains.length,
      },
      integrations: {
        openaiStatus: integrationsStatus.openai?.status || "Not Configured",
        openaiModel: integrationsStatus.openai?.model || "Not reported",
        furtherEnrichmentEnabled,
      },
      lastMessage: message ? sanitizedErrorMessage(message) : "",
      errors: errors.slice(0, 8).map(sanitizedErrorMessage),
      traces: debugTraces,
    };
  }

  async function loadSourceMemory() {
    if (!API_BASE) {
      setSourceMemoryStatus("Backend not configured.");
      return;
    }
    setSourceMemoryStatus("Loading stored sources...");
    try {
      const [sourceResponse, domainResponse, auditResponse] = await Promise.all([
        fetchStoredSources({ query: sourceSearch, sourceType: sourceTypeFilter, limit: 100 }),
        fetchPreferredDomains({ query: domainSearch, sourceType: domainTypeFilter, limit: 100 }),
        fetchProductKnowledgeBaseAudit(),
      ]);
      setStoredSources(sourceResponse.sources || []);
      setPreferredDomains(domainResponse.domains || []);
      setSourceMemoryAudit(auditResponse || {});
      setSourceMemoryBackend(auditResponse.product_knowledge_base_backend || sourceResponse.storage_backend || domainResponse.storage_backend || "unknown");
      setSourceMemoryStatus(`Loaded ${sourceResponse.sources?.length || 0} sources and ${domainResponse.domains?.length || 0} domains.`);
      recordDebugTrace({
        action: "load source memory",
        stage: "success",
        endpoint: "/settings/product-knowledge-base",
        message: `${sourceResponse.storage_backend || "unknown"} backend`,
      });
    } catch (error) {
      setSourceMemoryStatus(sanitizedErrorMessage(formatApiError(error)));
      recordDebugTrace({
        action: "load source memory",
        stage: "failed",
        endpoint: "/settings/product-knowledge-base",
        ...debugDetailsFromError(error),
      });
    }
  }

  async function handleSaveSource() {
    if (!sourceForm.brand.trim() || !sourceForm.model_sku.trim()) {
      setSourceMemoryStatus("Brand and model/SKU are required for an exact product source.");
      return;
    }
    try {
      const payload = { ...sourceForm, source_type: sourceForm.source_type || "manufacturer" };
      const response = editingSourceId
        ? await updateStoredSource(editingSourceId, payload)
        : await saveStoredSource(payload);
      setSourceMemoryBackend(response.storage_backend || sourceMemoryBackend);
      setSourceForm(emptySourceForm);
      setEditingSourceId("");
      setSourceMemoryStatus(editingSourceId ? "Stored source updated." : "Stored source saved.");
      await loadSourceMemory();
    } catch (error) {
      setSourceMemoryStatus(sanitizedErrorMessage(formatApiError(error)));
      recordDebugTrace({
        action: editingSourceId ? "update stored source" : "save stored source",
        stage: "failed",
        endpoint: "/settings/product-knowledge-base",
        ...debugDetailsFromError(error),
      });
    }
  }

  function handleEditSource(source: StoredProductSource) {
    setEditingSourceId(String(source.id || ""));
    setSourceForm({
      brand: String(source.brand || ""),
      model_sku: String(source.model_sku || ""),
      product_name: String(source.product_name || ""),
      product_page_url: String(source.product_page_url || ""),
      manufacturer_url: String(source.manufacturer_url || ""),
      spec_sheet_url: String(source.spec_sheet_url || ""),
      image_url: String(source.image_url || ""),
      dimension_source_url: String(source.dimension_source_url || ""),
      dimensions: String(source.dimensions || ""),
      width_in: String(source.width_in || ""),
      height_in: String(source.height_in || ""),
      depth_in: String(source.depth_in || ""),
      source_type: (String(source.source_type || "manufacturer") as SourceTypeFilter) || "manufacturer",
      notes: String(source.notes || ""),
    });
  }

  async function handleDeleteSource(source: StoredProductSource) {
    const sourceId = String(source.id || "");
    if (!sourceId) return;
    try {
      await deleteStoredSource(sourceId);
      setSourceMemoryStatus("Stored source deleted.");
      await loadSourceMemory();
    } catch (error) {
      setSourceMemoryStatus(sanitizedErrorMessage(formatApiError(error)));
    }
  }

  async function handleReverifySource(source: StoredProductSource) {
    const sourceId = String(source.id || "");
    if (!sourceId) return;
    try {
      await reverifyStoredSource(sourceId);
      setSourceMemoryStatus("Stored source marked for re-verification.");
      await loadSourceMemory();
    } catch (error) {
      setSourceMemoryStatus(sanitizedErrorMessage(formatApiError(error)));
    }
  }

  async function handleSaveDomain() {
    if (!domainForm.domain.trim()) {
      setSourceMemoryStatus("Domain is required.");
      return;
    }
    try {
      const payload = { ...domainForm, source_type: domainForm.source_type || "manufacturer" };
      const response = editingDomainId
        ? await updatePreferredDomain(editingDomainId, payload)
        : await savePreferredDomain(payload);
      setSourceMemoryBackend(response.storage_backend || sourceMemoryBackend);
      setDomainForm(emptyDomainForm);
      setEditingDomainId("");
      setSourceMemoryStatus(editingDomainId ? "Preferred domain updated." : "Preferred domain saved.");
      await loadSourceMemory();
    } catch (error) {
      setSourceMemoryStatus(sanitizedErrorMessage(formatApiError(error)));
      recordDebugTrace({
        action: editingDomainId ? "update preferred domain" : "save preferred domain",
        stage: "failed",
        endpoint: "/settings/preferred-domains",
        ...debugDetailsFromError(error),
      });
    }
  }

  function handleEditDomain(domain: PreferredSourceDomain) {
    setEditingDomainId(String(domain.id || ""));
    setDomainForm({
      domain: String(domain.domain || ""),
      source_type: (String(domain.source_type || "manufacturer") as SourceTypeFilter) || "manufacturer",
      notes: String(domain.notes || ""),
    });
  }

  async function handleDeleteDomain(domain: PreferredSourceDomain) {
    const domainId = String(domain.id || "");
    if (!domainId) return;
    try {
      await deletePreferredDomain(domainId);
      setSourceMemoryStatus("Preferred domain deleted.");
      await loadSourceMemory();
    } catch (error) {
      setSourceMemoryStatus(sanitizedErrorMessage(formatApiError(error)));
    }
  }

  async function copyDebugReport() {
    const report = JSON.stringify(buildDebugReport(), null, 2);
    try {
      await navigator.clipboard.writeText(report);
      setDebugCopyStatus("Debug report copied.");
    } catch {
      setDebugCopyStatus("Could not copy automatically. Select the debug text manually.");
    }
  }

  function buildEnrichmentDebugReportText({
    reportRows,
    response,
    action,
    finalStatus,
    extraErrors = [],
  }: {
    reportRows: IntakeRow[];
    response?: IntakeResponse;
    action: string;
    finalStatus: string;
    extraErrors?: string[];
  }) {
    const productRows = reportRows.filter(isProductRow);
    const manualRows = reportRows.filter((row) => !isProductRow(row));
    const groups = uniqueProductReportGroups(productRows);
    const duplicateGroups = groups.filter((group) => group.rows.length > 1);
    const timings = response?.stage_timings || {};
    const spent = getEstimatedCost(response);
    const budgetCap = stageNumber(response, "budget_cap_usd") ?? enrichmentBudgetUsd;
    const searchesUsed = stageNumber(response, "brave_searches_used") ?? 0;
    const costPerSearch = searchesUsed ? (stageNumber(response, "estimated_cost_usd") ?? 0) / searchesUsed : 0.006;
    const rowsMissingDimensions = stageNumber(response, "rows_missing_dimensions") ?? countRows(productRows, (row) => !hasComplete3dDimensions(row.Dimensions));
    const rowsMissingImages = stageNumber(response, "rows_missing_images") ?? countRows(productRows, (row) => !hasImage(row));
    const dimensionsFound = countRows(productRows, (row) => hasComplete3dDimensions(row.Dimensions));
    const imagesFound = countRows(productRows, hasImage);
    const knowledgeBaseHits = countRows(productRows, (row) => boolish(rowText(row, "knowledge_base_hit")) || boolish(rowText(row, "stored_source_hit")));
    const knowledgeBaseMisses = countRows(productRows, (row) => boolish(rowText(row, "knowledge_base_miss")));
    const knowledgeBaseUpdates = countRows(productRows, (row) => boolish(rowText(row, "knowledge_base_updated")) || boolish(rowText(row, "stored_source_updated")));
    const duplicateSearchesAvoidedFromKb = productRows.reduce((total, row) => total + (Number.parseInt(rowText(row, "duplicate_searches_avoided_from_cache") || "0", 10) || 0), 0);
    const furtherRowsSent = stageNumber(response, "further_enrichment_rows_sent") ?? 0;
    const furtherFieldsFilled = stageNumber(response, "further_enrichment_fields_filled") ?? 0;
    const furtherCost = stageNumber(response, "further_enrichment_cost_usd");
    const furtherSourceRows = productRows.filter((row) => rowText(row, "further_enrichment_sources").trim());
    const allErrors = [
      ...(response?.errors || []),
      ...extraErrors,
      ...debugTraces
        .filter((trace) => trace.stage === "failed")
        .map((trace) => `${trace.action}: ${trace.message || trace.statusCode || "failed"}`),
    ]
      .map((error) => sanitizedDebugText(error, 1200))
      .filter(Boolean);

    const productSections = groups.map((group, groupIndex) => {
      const representative =
        group.rows.find(({ row }) => rowText(row, "selected_product_url") || rowText(row, "Product URL") || rowText(row, "search_query_used")) ||
        group.rows[0];
      const row = representative.row;
      const queryText = rowText(row, "search_query_used");
      const candidates = parseReportCandidates(rowText(row, "product_url_candidates"));
      const selectedUrl = rowText(row, "selected_product_url") || rowText(row, "Product URL") || rowText(row, "dimension_source_url");
      const imageFound = hasImage(row);
      const dimensionsText = rowText(row, "dimensions_extracted") || rowText(row, "Dimensions");
      const dimensionsFound = Boolean(dimensionsText) && hasComplete3dDimensions(dimensionsText);
      const budgetText = rowText(row, "budget_spent");
      const searchMatch = budgetText.match(/searches=(\d+)\//);
      const rowSearches = searchMatch ? Number.parseInt(searchMatch[1] || "0", 10) : 0;
      const rowCost = rowSearches * costPerSearch;

      return [
        `Product ${groupIndex + 1}`,
        reportLine("normalized brand", group.normalizedBrand),
        reportLine("normalized model", group.normalizedModel),
        reportLine("original row numbers", group.rows.map(({ index }) => index + 1).join(", ")),
        reportLine("brand", rowText(row, "Brand")),
        reportLine("model", rowText(row, "Model/SKU")),
        reportLine("product name", rowText(row, "Product Name")),
        reportLine("search queries used", queryText),
        reportLine("sources checked", candidates.length ? candidates.join("\n  - ") : ""),
        reportLine("source selected", selectedUrl),
        reportLine("knowledge base hit", boolish(rowText(row, "knowledge_base_hit")) || boolish(rowText(row, "stored_source_hit")) ? "yes" : "no"),
        reportLine("stored source used", rowText(row, "knowledge_base_source_used") || rowText(row, "stored_source_used")),
        reportLine("stored source updated", boolish(rowText(row, "knowledge_base_updated")) || boolish(rowText(row, "stored_source_updated")) ? "yes" : "no"),
        reportLine("source rejected reason", rowText(row, "knowledge_base_rejected_reason") || rowText(row, "stored_source_rejected_reason")),
        rowText(row, "invalid_url_error")
          ? [
              "Invalid IPv6 URL / malformed URL",
              reportLine("Generated URL", rowText(row, "invalid_url_generated")),
              reportLine("Source", rowText(row, "invalid_url_source")),
              reportLine("Product", `${rowText(row, "Brand")} ${rowText(row, "Model/SKU")}`.trim() || rowText(row, "Product Name")),
              reportLine("Step", rowText(row, "invalid_url_step")),
              reportLine("Search query", rowText(row, "invalid_url_search_query")),
              reportLine("Normalized brand", rowText(row, "invalid_url_normalized_brand")),
              reportLine("Normalized model", rowText(row, "invalid_url_normalized_model")),
              reportLine("Fallback attempted", rowText(row, "invalid_url_fallback_attempted")),
              reportLine("Error", rowText(row, "invalid_url_error")),
            ].filter(Boolean).join("\n")
          : "",
        reportLine("preferred domain used", rowText(row, "preferred_domain_used")),
        reportLine("selected source domain", reportDomain(selectedUrl)),
        reportLine("image found", imageFound ? "yes" : "no"),
        reportLine("dimensions found", dimensionsFound ? "yes" : dimensionsText ? "partial/low confidence" : "no"),
        reportLine("extracted dimensions", dimensionsText),
        reportLine("dimension source", rowText(row, "dimension_source_url") || rowText(row, "Dimension Source URL")),
        reportLine("confidence score", rowText(row, "selected_product_url_confidence") || rowText(row, "Dimension Confidence") || rowText(row, "image_confidence") || rowText(row, "Confidence Score")),
        reportLine("further enrichment status", rowText(row, "further_enrichment_status")),
        reportLine("further enrichment model", rowText(row, "further_enrichment_model")),
        reportLine("further enrichment fields filled", rowText(row, "further_enrichment_fields_filled")),
        reportLine("further enrichment sources", rowText(row, "further_enrichment_sources")),
        reportLine("further enrichment row cost", rowText(row, "further_enrichment_cost_estimate")),
        reportLine("cost estimate", formatUsd(rowCost, 4)),
        reportLine("reason if failed", rowText(row, "further_enrichment_error") || rowText(row, "skipped_reason") || rowText(row, "enrichment_error") || rowText(row, "Suggested Action")),
      ].join("\n");
    });

    const suggestions = new Set<string>();
    if (rowsMissingDimensions) suggestions.add("Dimensions missed because verified manufacturer/spec data was not found or did not parse into complete W x H x D.");
    if (rowsMissingDimensions && imagesFound > dimensionsFound) suggestions.add("Several products found images but not dimensions. Try Dimension-focused retry or Spec sheet search.");
    if (duplicateGroups.length && !(stageNumber(response, "duplicate_rows_skipped_for_enrichment") || stageNumber(response, "duplicate_models_skipped"))) {
      suggestions.add("Duplicate models appear in the parsed rows. Confirm dedupe is avoiding repeated searches.");
    }
    if (rowsMissingImages) suggestions.add("Images are still missing for some products. Check selected source pages for gallery/metadata images.");
    if (allErrors.some((error) => /invalid ipv6 url|network|timeout|fetch|cors/i.test(error))) {
      suggestions.add("Some source requests failed at the network/URL layer. Review invalid URLs and retry with Debug Mode details.");
    }
    if ((stageNumber(response, "estimated_cost_usd") ?? 0) >= budgetCap * 0.95 && rowsMissingDimensions) {
      suggestions.add("Budget was nearly exhausted before all dimensions were found. Run a targeted spec-sheet retry or add preferred manufacturer domains.");
    }
    groups.forEach((group) => {
      const row = group.rows[0]?.row;
      if (!row) return;
      const selectedUrl = rowText(row, "selected_product_url") || rowText(row, "Product URL");
      const domain = reportDomain(selectedUrl);
      if (domain && rowText(row, "Dimensions") && rowText(row, "Image URL")) {
        suggestions.add(`Cache successful result for ${rowText(row, "Brand")} ${rowText(row, "Model/SKU")} from ${domain}.`);
      } else if (domain && !hasComplete3dDimensions(row.Dimensions)) {
        suggestions.add(`Add or down-rank ${domain} based on whether it reliably provides dimensions for this brand/category.`);
      }
    });
    if (!suggestions.size) suggestions.add("No obvious systemic failure detected. Compare per-product selected sources and confidence scores.");

    return [
      "Copy Debug Report for ChatGPT",
      "",
      "1. Run summary",
      reportLine("timestamp", new Date().toISOString()),
      reportLine("action", action),
      reportLine("uploaded file names", [
        ...files.map((file) => file.name),
        ...bulkImages.map((file) => file.name),
      ].join(", ") || "none"),
      reportLine("product links pasted", urls.split(/\r?\n/).filter((url) => url.trim()).length),
      reportLine("total parsed rows", reportRows.length),
      reportLine("unique products detected", groups.length),
      reportLine("rows ignored/manual review", manualRows.length),
      reportLine("budget cap", formatUsd(budgetCap)),
      reportLine("amount spent", spent),
      reportLine("final enrichment status", finalStatus),
      "",
      "2. Parse diagnostics",
      reportLine("rows detected", reportRows.length),
      reportLine("rows skipped", manualRows.length),
      reportLine("duplicate models detected", duplicateGroups.length),
      reportLine("price-only/manual-review rows", exportSummary.blank_price_only_rows.length || manualRows.filter(isReviewOnlyCharge).length),
      reportLine("header/footer/tax/total rows ignored", "not reported separately by parser; see skipped/manual review counts"),
      reportLine("suspected bad model tokens", exportSummary.phone_email_header_contamination.length),
      reportLine("duplicate rows removed before export", exportSummary.duplicate_rows_removed),
      "",
      "3. Enrichment steps",
      productSections.length ? productSections.join("\n\n") : "No unique product rows available.",
      "",
      "4. Cost diagnostics",
      reportLine("budget cap", formatUsd(budgetCap)),
      reportLine("amount spent", spent),
      reportLine("Brave/API searches", stageNumber(response, "brave_searches_used") ?? 0),
      reportLine("page fetches", stageNumber(response, "page_fetches_used") ?? 0),
      reportLine("unique products searched", stageNumber(response, "unique_products_searched") ?? 0),
      reportLine("cost per product", groups.length && stageNumber(response, "estimated_cost_usd") !== undefined ? formatUsd((stageNumber(response, "estimated_cost_usd") || 0) / groups.length, 4) : "not reported"),
      reportLine("duplicate searches avoided", stageNumber(response, "searches_avoided") ?? 0),
      reportLine("cache hits", stageNumber(response, "cache_hits") ?? 0),
      reportLine("knowledge base hits", knowledgeBaseHits),
      reportLine("knowledge base misses", knowledgeBaseMisses),
      reportLine("knowledge base updates", knowledgeBaseUpdates),
      reportLine("duplicate paid searches avoided from knowledge base", duplicateSearchesAvoidedFromKb),
      reportLine("failed searches/errors", allErrors.length),
      reportLine("cost per image found", imagesFound && stageNumber(response, "estimated_cost_usd") !== undefined ? formatUsd((stageNumber(response, "estimated_cost_usd") || 0) / imagesFound, 4) : "not reported"),
      reportLine("cost per dimension found", stageNumber(response, "cost_per_dimension_found") !== undefined ? formatUsd(stageNumber(response, "cost_per_dimension_found"), 4) : "not reported"),
      reportLine("dimensions found", dimensionsFound),
      reportLine("images found", imagesFound),
      reportLine("further enrichment enabled", furtherEnrichmentEnabled ? "yes" : "no"),
      reportLine("OpenAI used", furtherRowsSent > 0 ? "yes" : "no"),
      reportLine("OpenAI status", integrationsStatus.openai?.status || "Not Configured"),
      reportLine("OpenAI model", integrationsStatus.openai?.model || "Not reported"),
      reportLine("further enrichment cap", formatUsd(furtherEnrichmentBudgetUsd)),
      reportLine("further enrichment rows considered", stageNumber(response, "further_enrichment_rows_considered") ?? 0),
      reportLine("Rows sent to OpenAI", furtherRowsSent),
      reportLine("further enrichment rows updated", stageNumber(response, "further_enrichment_rows_updated") ?? 0),
      reportLine("Fields recovered by OpenAI", furtherFieldsFilled),
      reportLine("OpenAI cost estimate", furtherCost !== undefined ? formatUsd(furtherCost, 4) : "not reported"),
      reportLine("OpenAI cost per row", stageNumber(response, "further_enrichment_cost_per_row_usd") !== undefined ? formatUsd(stageNumber(response, "further_enrichment_cost_per_row_usd"), 4) : "not reported"),
      reportLine("OpenAI source links returned", furtherSourceRows.length ? furtherSourceRows.map((row) => rowText(row, "further_enrichment_sources")).join("\n") : "none"),
      reportLine("backend timing keys", Object.keys(timings).join(", ")),
      "",
      "5. Errors",
      allErrors.length ? allErrors.map((error) => `- ${error}`).join("\n") : "No raw technical errors recorded.",
      "",
      "6. Improvement suggestions",
      Array.from(suggestions).map((suggestion) => `- ${suggestion}`).join("\n"),
    ].join("\n");
  }

  async function copyEnrichmentDebugReport() {
    try {
      await navigator.clipboard.writeText(enrichmentDebugReport);
      setEnrichmentDebugReportStatus("Enrichment debug report copied.");
    } catch {
      setEnrichmentDebugReportStatus("Could not copy automatically. Select the report text manually.");
    }
  }

  function downloadEnrichmentDebugReport() {
    const today = new Date().toISOString().slice(0, 10);
    downloadBlob(
      new Blob([enrichmentDebugReport], { type: "text/plain;charset=utf-8" }),
      `sch_enrichment_debug_report_${today}.txt`,
    );
  }

  function handleFileSelection(selectedFiles: FileList | null) {
    setUploadError("");
    try {
      const nextFiles = Array.from(selectedFiles ?? []);
      const invalidFile = nextFiles.find(
        (file) => file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf"),
      );
      if (invalidFile) {
        setFiles([]);
        setUploadError("Only PDF files can be uploaded.");
        recordDebugTrace({
          action: "pdf upload",
          stage: "validation failed",
          message: `Rejected unsupported file type: ${invalidFile.name}`,
        });
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }
      setFiles(nextFiles);
      setWorkflowStage("upload");
      setParseReviewed(false);
      setEnrichmentReviewed(false);
      setExportGenerated(false);
      recordDebugTrace({
        action: "pdf upload",
        stage: "selected",
        message: `${nextFiles.length} PDF file(s) selected`,
      });
    } catch {
      setFiles([]);
      setUploadError("Upload failed. Please choose the PDF again.");
      recordDebugTrace({
        action: "pdf upload",
        stage: "failed",
        message: "File selection failed in browser.",
      });
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function removeFile(index: number) {
    setUploadError("");
    setFiles((current) => {
      const nextFiles = current.filter((_, fileIndex) => fileIndex !== index);
      if (nextFiles.length === 0 && fileInputRef.current) fileInputRef.current.value = "";
      return nextFiles;
    });
  }

  function handleBulkImageSelection(selectedFiles: FileList | File[] | null) {
    setBulkImageError("");
    const nextFiles = Array.from(selectedFiles ?? []);
    const accepted = new Set(["image/jpeg", "image/png", "image/webp"]);
    const invalidFile = nextFiles.find((file) => {
      const name = file.name.toLowerCase();
      return !accepted.has(file.type) && !/\.(jpe?g|png|webp)$/.test(name);
    });
    if (invalidFile) {
      setBulkImageError("Only JPG, PNG, and WebP images can be uploaded.");
      recordDebugTrace({
        action: "bulk photo upload",
        stage: "validation failed",
        message: `Rejected unsupported image type: ${invalidFile.name}`,
      });
      return;
    }
    setBulkImages(nextFiles);
    setPhotoBulkResults({});
    setPhotoBulkSummary({ success: 0, failed: 0 });
    setWorkflowStage("upload");
    setParseReviewed(false);
    setEnrichmentReviewed(false);
    setExportGenerated(false);
    recordDebugTrace({
      action: "bulk photo upload",
      stage: "selected",
      message: `${nextFiles.length} image file(s) selected`,
    });
  }

  function clearBulkImages() {
    setBulkImages([]);
    setBulkImageError("");
    setPhotoBulkResults({});
    setPhotoBulkSummary({ success: 0, failed: 0 });
    setWorkflowStage("upload");
    setParseReviewed(false);
    setEnrichmentReviewed(false);
    setExportGenerated(false);
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

  async function createBulkPhotoRows(startIndex: number) {
    const nextResults: typeof photoBulkResults = {};
    let success = 0;
    let failed = 0;
    const createdRows: IntakeRow[] = [];
    setLastEndpoint(`${API_BASE || "not configured"}/api/upload-image`);

    for (const [index, file] of bulkImages.entries()) {
      const key = bulkImageKey(file, index);
      nextResults[key] = { status: "queued" };
      setPhotoBulkResults({ ...nextResults });
      try {
        const response = await uploadImage(file);
        const secureUrl = response.secure_url || "";
        if (!isPublicHttpsImageUrl(secureUrl)) throw new Error("Cloudinary did not return a public HTTPS URL.");
        const row = createPhotoOnlyRow(file, index, secureUrl, "Needs Review");
        createdRows.push(row);
        nextResults[key] = { status: "uploaded", url: secureUrl, rowIndex: startIndex + createdRows.length - 1 };
        success += 1;
        setLastSuccessfulStage("Cloudinary image upload");
        recordDebugTrace({
          action: "cloudinary image upload",
          stage: "success",
          endpoint: "/api/upload-image",
          itemId: String(startIndex + createdRows.length - 1),
          message: `${file.name} uploaded`,
        });
      } catch (error) {
        const row = createPhotoOnlyRow(file, index, "", "Missing Image");
        createdRows.push(row);
        nextResults[key] = {
          status: "failed",
          error: error instanceof Error ? error.message : "Upload failed.",
          rowIndex: startIndex + createdRows.length - 1,
        };
        failed += 1;
        recordDebugTrace({
          action: "cloudinary image upload",
          stage: "failed",
          endpoint: "/api/upload-image",
          itemId: String(startIndex + createdRows.length - 1),
          ...debugDetailsFromError(error),
        });
      }
      setPhotoBulkResults({ ...nextResults });
      setPhotoBulkSummary({ success, failed });
    }

    return { createdRows, success, failed };
  }

  async function handlePhotoBulkCreate() {
    if (!bulkImages.length) {
      setBulkImageError("Choose at least one image first.");
      return;
    }
    const selectedSection = photoBulkSection === "__custom__" ? photoBulkCustomSection.trim() : photoBulkSection;
    if (!selectedSection.trim()) {
      setBulkImageError("Choose a section before creating rows.");
      return;
    }
    setBusy("photoBulk");
    setBulkImageError("");
    setMessage("");
    let createdRows: IntakeRow[] = [];
    let success = 0;
    let failed = 0;
    try {
      ({ createdRows, success, failed } = await createBulkPhotoRows(rows.length));
      const response = await validateRows([...rows, ...createdRows]);
      setRows(response.rows);
      setErrors(response.errors);
      setMessage(`Photo-only bulk import created ${success} row${success === 1 ? "" : "s"} with images; ${failed} failed.`);
      setWorkflowStage("reviewParsed");
    } catch {
      setRows((current) => [...current, ...createdRows]);
      setMessage(`Photo-only bulk import created ${success} row${success === 1 ? "" : "s"} with images; ${failed} failed.`);
      setWorkflowStage("reviewParsed");
    } finally {
      setBusy("");
    }
  }

  async function retryPhotoUpload(file: File, index: number) {
    const key = bulkImageKey(file, index);
    const result = photoBulkResults[key];
    if (!result || result.rowIndex === undefined) return;
    setLastEndpoint(`${API_BASE || "not configured"}/api/upload-image`);
    setPhotoBulkResults((current) => ({ ...current, [key]: { ...result, status: "queued", error: "" } }));
    try {
      const response = await uploadImage(file);
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
      setLastSuccessfulStage("Cloudinary image retry");
      recordDebugTrace({
        action: "cloudinary image retry",
        stage: "success",
        endpoint: "/api/upload-image",
        itemId: String(result.rowIndex),
        message: `${file.name} uploaded on retry`,
      });
    } catch (error) {
      setPhotoBulkResults((current) => ({
        ...current,
        [key]: {
          status: "failed",
          error: error instanceof Error ? error.message : "Upload failed.",
          rowIndex: result.rowIndex,
        },
      }));
      recordDebugTrace({
        action: "cloudinary image retry",
        stage: "failed",
        endpoint: "/api/upload-image",
        itemId: String(result.rowIndex),
        ...debugDetailsFromError(error),
      });
    }
  }

  async function handleProductImageUpload(rowIndex: number, file: File | undefined) {
    if (!file) return;
    setLastEndpoint(`${API_BASE || "not configured"}/api/upload-image`);
    setProductImageUploads((current) => ({ ...current, [rowIndex]: "Uploading..." }));
    try {
      const response = await uploadImage(file);
      const secureUrl = response.secure_url || "";
      if (!isPublicHttpsImageUrl(secureUrl)) throw new Error("Upload did not return a public image URL.");
      setRows((current) =>
        current.map((row, index) =>
          index === rowIndex
            ? {
                ...row,
                "Image URL": secureUrl,
                "Image Upload Status": "Uploaded",
              }
            : row,
        ),
      );
      setProductImageUploads((current) => ({ ...current, [rowIndex]: "" }));
      setLastSuccessfulStage("Product image upload");
      recordDebugTrace({
        action: "product image upload",
        stage: "success",
        endpoint: "/api/upload-image",
        itemId: String(rowIndex),
      });
    } catch (error) {
      setProductImageUploads((current) => ({
        ...current,
        [rowIndex]: error instanceof Error ? error.message : "Upload failed.",
      }));
      recordDebugTrace({
        action: "product image upload",
        stage: "failed",
        endpoint: "/api/upload-image",
        itemId: String(rowIndex),
        ...debugDetailsFromError(error),
      });
    }
  }

  async function handleGenerate() {
    setBusy("generate");
    setMessage("");
    setErrors([]);
    setWorkflowStage("parse");
    setParseReviewed(false);
    setEnrichmentReviewed(false);
    setExportGenerated(false);
    setParseStatus("Parsing uploaded files and links...");
    setLastEndpoint(`${API_BASE || "not configured"}/intake/generate`);
    recordDebugTrace({
      action: "parse files",
      stage: "started",
      endpoint: "/intake/generate",
      message: `${files.length} PDFs, ${bulkImages.length} photos, ${urls.split(/\r?\n/).filter((url) => url.trim()).length} URLs`,
    });
    try {
      let parsedRows: IntakeRow[] = [];
      let parseErrors: string[] = [];
      let cost = "Not reported";

      if (files.length || urls.trim()) {
        const response = await generateIntakeTable({ project, room, urls, useAiPdf, files });
        parsedRows = response.rows;
        parseErrors = response.errors;
        cost = getEstimatedCost(response);
        const storageWarnings = response.stage_timings?.storage_warnings;
        if (Array.isArray(storageWarnings) && storageWarnings.length) {
          recordDebugTrace({
            action: "parse files",
            stage: "warning",
            endpoint: "/intake/generate",
            message: JSON.stringify({ storage_warnings: storageWarnings }),
          });
        }
      }

      let photoMessage = "";
      if (bulkImages.length) {
        const selectedSection = photoBulkSection === "__custom__" ? photoBulkCustomSection.trim() : photoBulkSection;
        if (!selectedSection.trim()) {
          throw new Error("Choose a section before parsing product photos.");
        }
        setParseStatus("Uploading photos to Cloudinary...");
        const { createdRows, success, failed } = await createBulkPhotoRows(parsedRows.length);
        parsedRows = [...parsedRows, ...createdRows];
        photoMessage = ` Photo rows: ${success} uploaded, ${failed} failed.`;
      }

      let finalRows = parsedRows;
      let finalErrors = parseErrors;
      if (parsedRows.length) {
        try {
          const validated = await validateRows(parsedRows);
          finalRows = validated.rows;
          finalErrors = [...parseErrors, ...validated.errors];
        } catch {
          finalRows = parsedRows;
        }
      }

      setRows(finalRows);
      setErrors(finalErrors);
      const finalProductRows = finalRows.filter(isProductRow);
      const heldRows = Math.max(0, finalRows.length - finalProductRows.length);
      setParseStatus(
        `Parse complete: ${finalProductRows.length} product${finalProductRows.length === 1 ? "" : "s"} found${
          heldRows ? `; ${heldRows} manual-review row${heldRows === 1 ? "" : "s"} held.` : "."
        }`,
      );
      setParsedProductsOpen(false);
      setEnrichedProductsOpen(false);
      setEstimatedCost(cost);
      setWorkflowStage("reviewParsed");
      setMessage(`Parsed products are ready for review.${photoMessage}`);
      setLastSuccessfulStage("Parse complete");
      recordDebugTrace({
        action: "parse files",
        stage: "success",
        endpoint: "/intake/generate",
        message: `${finalProductRows.length} product rows parsed; ${heldRows} held row(s); ${finalErrors.length} error(s)`,
      });
    } catch (error) {
      const formatted = userFacingApiMessage(error, debugMode);
      setParseStatus("Parse failed.");
      setWorkflowStage("upload");
      setMessage(formatted);
      recordDebugTrace({
        action: "parse files",
        stage: "failed",
        endpoint: "/intake/generate",
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  async function handleValidate() {
    setBusy("validate");
    setMessage("");
    setWorkflowStage("enrich");
    setEnrichmentReviewed(false);
    setExportGenerated(false);
    setEnrichmentStatus("Enriching missing product data...");
    setEstimatedCost(`$0.0000 spent · capped at ${formatUsd(enrichmentBudgetUsd)}`);
    const beforeImages = countRows(includedRows, hasImage);
    const beforeDimensions = countRows(includedRows, (row) => hasComplete3dDimensions(row.Dimensions));
    setLastEndpoint(`${API_BASE || "not configured"}/intake/enrich`);
    recordDebugTrace({
      action: "enrich missing data",
      stage: "started",
      endpoint: "/intake/enrich",
      message: `mode=${enrichmentMode}; forceRefresh=${forceRefreshEnrichment}; cap=${formatUsd(enrichmentBudgetUsd)}`,
    });
    try {
      const response = await enrichRows({
        rows,
        useWebEnrichment,
        enrichmentMode,
        forceRefresh: forceRefreshEnrichment,
        enrichmentBudgetUsd,
      });
      setRows(response.rows);
      setErrors(response.errors);
      const enrichedRows = response.rows.filter(isProductRow);
      const afterImages = countRows(enrichedRows, hasImage);
      const afterDimensions = countRows(enrichedRows, (row) => hasComplete3dDimensions(row.Dimensions));
      const unresolved = countRows(enrichedRows, (row) => missingFieldsForRow(row).length > 0);
      const averageConfidence = stageNumber(response, "average_confidence");
      const missingDimensionsAfter = Math.round(
        stageNumber(response, "rows_missing_dimensions") ??
          countRows(enrichedRows, (row) => !hasComplete3dDimensions(row.Dimensions)),
      );
      const missingImagesAfter = Math.round(
        stageNumber(response, "rows_missing_images") ?? countRows(enrichedRows, (row) => !hasImage(row)),
      );
      const cacheHits = Math.round(stageNumber(response, "cache_hits") ?? 0);
      const duplicateModelsSkipped = Math.round(stageNumber(response, "duplicate_models_skipped") ?? 0);
      const searchesAvoided = Math.round(stageNumber(response, "searches_avoided") ?? duplicateModelsSkipped);
      const usefulFieldsFound = Math.round(stageNumber(response, "useful_fields_found") ?? 0);
      const costPerUsefulField = stageNumber(response, "cost_per_useful_field");
      const costPerDimensionFound = stageNumber(response, "cost_per_dimension_found");
      const budgetCap = stageNumber(response, "budget_cap_usd") ?? enrichmentBudgetUsd;
      const uniqueProductsSearched = Math.round(stageNumber(response, "unique_products_searched") ?? 0);
      const dimensionsFoundPerDollar = stageNumber(response, "dimensions_found_per_dollar");
      const imagesFoundPerDollar = stageNumber(response, "images_found_per_dollar");
      const failedBeforeCompletion = response.errors.some((error) =>
        error.toLowerCase().includes("enrichment failed before completion"),
      );
      const partialEnrichment = !failedBeforeCompletion && useWebEnrichment && (missingDimensionsAfter > 0 || missingImagesAfter > 0);
      const finalStatus = failedBeforeCompletion
        ? "Enrichment failed before completion."
        : partialEnrichment
          ? missingDimensionsAfter > 0
            ? "Partially enriched — dimensions still missing."
            : "Partially enriched — images still missing."
        : useWebEnrichment
          ? "Enrichment complete."
          : "Input updates saved without web enrichment.";
      const finalMessage = failedBeforeCompletion
        ? "Enrichment failed before completion. Review debug details before exporting."
        : partialEnrichment
          ? `Partially enriched: ${missingDimensionsAfter} product${missingDimensionsAfter === 1 ? "" : "s"} still missing dimensions and ${missingImagesAfter} missing image${missingImagesAfter === 1 ? "" : "s"}.`
        : useWebEnrichment
          ? "Missing info search complete."
          : "Input updates saved without web search.";
      setEnrichmentStats({
        filledImages: Math.max(0, afterImages - beforeImages),
        filledDimensions: Math.max(0, afterDimensions - beforeDimensions),
        unresolved,
        rowsEnriched: Math.round(stageNumber(response, "rows_enriched") ?? enrichedRows.length - unresolved),
        missingDimensions: missingDimensionsAfter,
        missingImages: missingImagesAfter,
        averageConfidence: averageConfidence === undefined ? "Not reported" : `${Math.round(averageConfidence * 100)}%`,
        cacheHits,
        duplicateModelsSkipped,
        searchesAvoided,
        usefulFieldsFound,
        costPerUsefulField: costPerUsefulField === undefined ? "Not reported" : `$${costPerUsefulField.toFixed(4)}`,
        costPerDimensionFound: costPerDimensionFound === undefined ? "Not reported" : `$${costPerDimensionFound.toFixed(4)}`,
        budgetCap: formatUsd(budgetCap),
        uniqueProductsSearched,
        dimensionsFoundPerDollar: dimensionsFoundPerDollar === undefined ? "Not reported" : `${dimensionsFoundPerDollar.toFixed(1)}/$`,
        imagesFoundPerDollar: imagesFoundPerDollar === undefined ? "Not reported" : `${imagesFoundPerDollar.toFixed(1)}/$`,
      });
      setEstimatedCost(`${getEstimatedCost(response)} spent · ${formatUsd(budgetCap)} cap`);
      setEnrichmentStatus(finalStatus);
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: response.rows,
          response,
          action: "Enrich Missing Data",
          finalStatus,
        }),
      );
      setEnrichmentDebugReportStatus("");
      setEnrichedProductsOpen(false);
      setWorkflowStage("reviewEnriched");
      setMessage(finalMessage);
      setLastSuccessfulStage(failedBeforeCompletion ? "Enrichment failed before completion" : partialEnrichment ? "Partially enriched" : "Enrichment complete");
      recordDebugTrace({
        action: "enrich missing data",
        stage: failedBeforeCompletion ? "failed" : "success",
        endpoint: "/intake/enrich",
        message: failedBeforeCompletion
          ? JSON.stringify({
              errors: response.errors,
              diagnostics: response.dimension_diagnostics ?? [],
            })
          : `${afterImages - beforeImages} image delta; ${afterDimensions - beforeDimensions} dimension delta; ${unresolved} unresolved; ${searchesAvoided} searches avoided`,
      });
      const storageWarnings = response.stage_timings?.storage_warnings;
      if (Array.isArray(storageWarnings) && storageWarnings.length) {
        recordDebugTrace({
          action: "enrich missing data",
          stage: "warning",
          endpoint: "/intake/enrich",
          message: JSON.stringify({ storage_warnings: storageWarnings }),
        });
      }
    } catch (error) {
      const formatted = userFacingApiMessage(error, debugMode);
      setEnrichmentStatus("Enrichment failed.");
      setWorkflowStage("reviewParsed");
      setMessage(formatted);
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: rows,
          action: "Enrich Missing Data",
          finalStatus: "Enrichment failed.",
          extraErrors: [sanitizedDebugText(formatApiError(error), 2000)],
        }),
      );
      setEnrichmentDebugReportStatus("");
      recordDebugTrace({
        action: "enrich missing data",
        stage: "failed",
        endpoint: "/intake/enrich",
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  async function handleTargetedRetry(label: string, mode: EnrichmentMode, forceRefresh = false) {
    setBusy("validate");
    setMessage("");
    setWorkflowStage("enrich");
    setEnrichmentStatus(`${label} running...`);
    setEstimatedCost(`$0.0000 spent · capped at ${formatUsd(enrichmentBudgetUsd)}`);
    const beforeImages = countRows(includedRows, hasImage);
    const beforeDimensions = countRows(includedRows, (row) => hasComplete3dDimensions(row.Dimensions));
    setLastEndpoint(`${API_BASE || "not configured"}/intake/enrich`);
    recordDebugTrace({
      action: label,
      stage: "started",
      endpoint: "/intake/enrich",
      message: `mode=${mode}; forceRefresh=${forceRefresh}; cap=${formatUsd(enrichmentBudgetUsd)}; missingDimensions=${missingDimensionsCount}; missingImages=${missingImageCount}`,
    });
    try {
      const response = await enrichRows({
        rows,
        useWebEnrichment: true,
        enrichmentMode: mode,
        forceRefresh,
        enrichmentBudgetUsd,
      });
      setRows(response.rows);
      setErrors(response.errors);
      const enrichedRows = response.rows.filter(isProductRow);
      const afterImages = countRows(enrichedRows, hasImage);
      const afterDimensions = countRows(enrichedRows, (row) => hasComplete3dDimensions(row.Dimensions));
      const missingDimensionsAfter = Math.round(
        stageNumber(response, "rows_missing_dimensions") ??
          countRows(enrichedRows, (row) => !hasComplete3dDimensions(row.Dimensions)),
      );
      const missingImagesAfter = Math.round(
        stageNumber(response, "rows_missing_images") ?? countRows(enrichedRows, (row) => !hasImage(row)),
      );
      const averageConfidence = stageNumber(response, "average_confidence");
      const usefulFieldsFound = Math.round(stageNumber(response, "useful_fields_found") ?? 0);
      const costPerDimensionFound = stageNumber(response, "cost_per_dimension_found");
      const budgetCap = stageNumber(response, "budget_cap_usd") ?? enrichmentBudgetUsd;
      const failedBeforeCompletion = response.errors.some((error) =>
        error.toLowerCase().includes("enrichment failed before completion"),
      );
      const finalStatus = failedBeforeCompletion
        ? `${label} failed before completion.`
        : missingDimensionsAfter > 0
          ? "Partially enriched — dimensions still missing."
          : missingImagesAfter > 0
            ? "Partially enriched — images still missing."
            : `${label} complete.`;
      const finalMessage = failedBeforeCompletion
        ? `${label} failed before completion. Turn on Debug Mode for technical details.`
        : `${label} finished: ${Math.max(0, afterDimensions - beforeDimensions)} dimension and ${Math.max(0, afterImages - beforeImages)} image update${Math.max(0, afterDimensions - beforeDimensions) + Math.max(0, afterImages - beforeImages) === 1 ? "" : "s"}.`;
      setEnrichmentStats({
        filledImages: Math.max(0, afterImages - beforeImages),
        filledDimensions: Math.max(0, afterDimensions - beforeDimensions),
        unresolved: countRows(enrichedRows, (row) => missingFieldsForRow(row).length > 0),
        rowsEnriched: Math.round(stageNumber(response, "rows_enriched") ?? 0),
        missingDimensions: missingDimensionsAfter,
        missingImages: missingImagesAfter,
        averageConfidence: averageConfidence === undefined ? "Not reported" : `${Math.round(averageConfidence * 100)}%`,
        cacheHits: Math.round(stageNumber(response, "cache_hits") ?? 0),
        duplicateModelsSkipped: Math.round(stageNumber(response, "duplicate_models_skipped") ?? 0),
        searchesAvoided: Math.round(stageNumber(response, "searches_avoided") ?? 0),
        usefulFieldsFound,
        costPerUsefulField:
          stageNumber(response, "cost_per_useful_field") === undefined
            ? "Not reported"
            : `$${(stageNumber(response, "cost_per_useful_field") ?? 0).toFixed(4)}`,
        dimensionsFoundPerDollar:
          stageNumber(response, "dimensions_found_per_dollar") === undefined
            ? "Not reported"
            : `${(stageNumber(response, "dimensions_found_per_dollar") ?? 0).toFixed(1)}/$`,
        imagesFoundPerDollar:
          stageNumber(response, "images_found_per_dollar") === undefined
            ? "Not reported"
            : `${(stageNumber(response, "images_found_per_dollar") ?? 0).toFixed(1)}/$`,
        costPerDimensionFound: costPerDimensionFound === undefined ? "Not reported" : `$${costPerDimensionFound.toFixed(4)}`,
        budgetCap: formatUsd(budgetCap),
        uniqueProductsSearched: Math.round(stageNumber(response, "unique_products_searched") ?? 0),
      });
      setEstimatedCost(`${getEstimatedCost(response)} spent · ${formatUsd(budgetCap)} cap`);
      setEnrichmentStatus(finalStatus);
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: response.rows,
          response,
          action: label,
          finalStatus,
        }),
      );
      setEnrichmentDebugReportStatus("");
      setWorkflowStage("reviewEnriched");
      setMessage(finalMessage);
      setLastSuccessfulStage(failedBeforeCompletion ? `${label} failed` : `${label} complete`);
      recordDebugTrace({
        action: label,
        stage: failedBeforeCompletion ? "failed" : "success",
        endpoint: "/intake/enrich",
        message: `${missingDimensionsAfter} missing dimensions; ${missingImagesAfter} missing images; cost=${getEstimatedCost(response)}`,
      });
    } catch (error) {
      setEnrichmentStatus(`${label} failed.`);
      setWorkflowStage("reviewEnriched");
      setMessage(userFacingApiMessage(error, debugMode));
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: rows,
          action: label,
          finalStatus: `${label} failed.`,
          extraErrors: [sanitizedDebugText(formatApiError(error), 2000)],
        }),
      );
      setEnrichmentDebugReportStatus("");
      recordDebugTrace({
        action: label,
        stage: "failed",
        endpoint: "/intake/enrich",
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  async function handleFurtherEnrichment() {
    if (!furtherEnrichmentEnabled) {
      setMessage("Further Enrichment is off. Enable it in Settings before running AI fallback research.");
      setSettingsOpen(true);
      return;
    }
    setBusy("furtherEnrichment");
    setMessage("");
    setWorkflowStage("enrich");
    setEnrichmentStatus("Further enrichment running on incomplete rows...");
    setEstimatedCost(`${formatUsd(furtherEnrichmentEstimatedCost, 4)} estimated · capped at ${formatUsd(furtherEnrichmentBudgetUsd)}`);
    const beforeImages = countRows(includedRows, hasImage);
    const beforeDimensions = countRows(includedRows, (row) => hasComplete3dDimensions(row.Dimensions));
    setLastEndpoint(`${API_BASE || "not configured"}/intake/further-enrich`);
    recordDebugTrace({
      action: "further enrich missing fields",
      stage: "started",
      endpoint: "/intake/further-enrich",
      message: `candidates=${furtherEnrichmentCandidateCount}; estimated=${formatUsd(furtherEnrichmentEstimatedCost, 4)}; cap=${formatUsd(furtherEnrichmentBudgetUsd)}`,
    });
    try {
      const response = await furtherEnrichRows({
        rows,
        enabled: furtherEnrichmentEnabled,
        maxCostUsd: furtherEnrichmentBudgetUsd,
      });
      setRows(response.rows);
      setErrors(response.errors);
      const enrichedRows = response.rows.filter(isProductRow);
      const afterImages = countRows(enrichedRows, hasImage);
      const afterDimensions = countRows(enrichedRows, (row) => hasComplete3dDimensions(row.Dimensions));
      const missingDimensionsAfter = countRows(enrichedRows, (row) => !hasComplete3dDimensions(row.Dimensions));
      const missingImagesAfter = countRows(enrichedRows, (row) => !hasImage(row));
      const rowsUpdated = Math.round(stageNumber(response, "further_enrichment_rows_updated") ?? 0);
      const fieldsFilled = Math.round(stageNumber(response, "further_enrichment_fields_filled") ?? 0);
      const cap = stageNumber(response, "further_enrichment_estimated_cost_usd") ?? furtherEnrichmentEstimatedCost;
      const actualCost = stageNumber(response, "further_enrichment_cost_usd") ?? 0;
      const finalStatus =
        rowsUpdated > 0
          ? `Further enrichment updated ${rowsUpdated} row${rowsUpdated === 1 ? "" : "s"}.`
          : response.errors.length
            ? "Further enrichment did not update rows."
            : "Further enrichment found no safe high-confidence updates.";
      setEnrichmentStats((current) => ({
        ...current,
        filledImages: Math.max(0, afterImages - beforeImages),
        filledDimensions: Math.max(0, afterDimensions - beforeDimensions),
        unresolved: countRows(enrichedRows, (row) => missingFieldsForRow(row).length > 0),
        missingDimensions: missingDimensionsAfter,
        missingImages: missingImagesAfter,
        usefulFieldsFound: fieldsFilled || current.usefulFieldsFound,
        budgetCap: formatUsd(furtherEnrichmentBudgetUsd),
      }));
      setEstimatedCost(`${formatUsd(actualCost, 4)} spent · ${formatUsd(furtherEnrichmentBudgetUsd)} cap`);
      setEnrichmentStatus(finalStatus);
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: response.rows,
          response,
          action: "Further Enrich Missing Fields",
          finalStatus,
        }),
      );
      setEnrichmentDebugReportStatus("");
      setWorkflowStage("reviewEnriched");
      setMessage(
        rowsUpdated > 0
          ? `Further enrichment filled ${fieldsFilled} field${fieldsFilled === 1 ? "" : "s"} across ${rowsUpdated} row${rowsUpdated === 1 ? "" : "s"}.`
          : response.errors.length
            ? userFacingApiMessage(new Error(response.errors[0]), debugMode)
            : "Further enrichment finished without safe writebacks. Existing high-confidence data was preserved.",
      );
      setLastSuccessfulStage(rowsUpdated > 0 ? "Further enrichment complete" : "Further enrichment reviewed");
      recordDebugTrace({
        action: "further enrich missing fields",
        stage: response.errors.length && rowsUpdated === 0 ? "failed" : "success",
        endpoint: "/intake/further-enrich",
        message: `rowsUpdated=${rowsUpdated}; fieldsFilled=${fieldsFilled}; cost=${formatUsd(actualCost, 4)}; estimated=${formatUsd(cap, 4)}; missingDimensions=${missingDimensionsAfter}; missingImages=${missingImagesAfter}`,
      });
    } catch (error) {
      setEnrichmentStatus("Further enrichment failed.");
      setWorkflowStage("reviewEnriched");
      setMessage(userFacingApiMessage(error, debugMode));
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: rows,
          action: "Further Enrich Missing Fields",
          finalStatus: "Further enrichment failed.",
          extraErrors: [sanitizedDebugText(formatApiError(error), 2000)],
        }),
      );
      setEnrichmentDebugReportStatus("");
      recordDebugTrace({
        action: "further enrich missing fields",
        stage: "failed",
        endpoint: "/intake/further-enrich",
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  async function handleRecoverMissingImages() {
    setBusy("imageRecovery");
    setMessage("");
    setEnrichmentStatus("Recovering missing images from verified product pages...");
    const beforeImages = countRows(includedRows, hasImage);
    setLastEndpoint(`${API_BASE || "not configured"}/intake/recover-images`);
    recordDebugTrace({
      action: "recover missing images",
      stage: "started",
      endpoint: "/intake/recover-images",
      message: `mode=${enrichmentMode}; missingImages=${missingImageCount}`,
    });
    try {
      const response = await recoverMissingImages({
        rows,
        enrichmentMode,
        forceRefresh: forceRefreshEnrichment,
      });
      setRows(response.rows);
      setErrors(response.errors);
      const enrichedRows = response.rows.filter(isProductRow);
      const afterImages = countRows(enrichedRows, hasImage);
      const imageDelta = Math.max(0, afterImages - beforeImages);
      const unresolved = countRows(enrichedRows, (row) => missingFieldsForRow(row).length > 0);
      setEnrichmentStats((current) => ({
        ...current,
        filledImages: current.filledImages + imageDelta,
        unresolved,
      }));
      setEstimatedCost(getEstimatedCost(response));
      setEnrichmentStatus("Missing image recovery complete.");
      setWorkflowStage("reviewEnriched");
      setMessage(`Image recovery complete: ${imageDelta} image${imageDelta === 1 ? "" : "s"} added.`);
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: response.rows,
          response,
          action: "Recover Missing Images",
          finalStatus: "Missing image recovery complete.",
        }),
      );
      setEnrichmentDebugReportStatus("");
      setLastSuccessfulStage("Missing image recovery complete");
      recordDebugTrace({
        action: "recover missing images",
        stage: "success",
        endpoint: "/intake/recover-images",
        message: `${imageDelta} image(s) added`,
      });
    } catch (error) {
      setEnrichmentStatus("Image recovery failed.");
      setMessage(userFacingApiMessage(error, debugMode));
      setEnrichmentDebugReport(
        buildEnrichmentDebugReportText({
          reportRows: rows,
          action: "Recover Missing Images",
          finalStatus: "Image recovery failed.",
          extraErrors: [sanitizedDebugText(formatApiError(error), 2000)],
        }),
      );
      setEnrichmentDebugReportStatus("");
      recordDebugTrace({
        action: "recover missing images",
        stage: "failed",
        endpoint: "/intake/recover-images",
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  function downloadBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function handleJsonExport() {
    const today = new Date().toISOString().slice(0, 10);
    const payload = {
      generatedAt: new Date().toISOString(),
      project,
      defaultRoom: room,
      rows: includedRows,
      exportSummary,
      estimatedCost,
    };
    downloadBlob(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      `programa_import_${today}.json`,
    );
    setWorkflowStage("export");
    setExportGenerated(true);
    setMessage("JSON export generated.");
    setLastSuccessfulStage("JSON export generated");
    recordDebugTrace({
      action: "json export",
      stage: "success",
      message: `${includedRows.length} included rows`,
    });
  }

  async function handleProgramaExport(format: "csv" | "xlsx" | "zip" | "debug") {
    setBusy("export");
    setMessage("");
    const endpoint =
      format === "xlsx"
        ? "/export/programa/xlsx"
        : format === "zip"
          ? "/export/programa/zip"
          : format === "debug"
            ? "/export/programa/debug-csv"
            : "/export/programa/csv";
    setLastEndpoint(`${API_BASE || "not configured"}${endpoint}`);
    recordDebugTrace({
      action: "programa export",
      stage: "started",
      endpoint,
      message: `${format} export; ${includedRows.length} included rows`,
    });
    try {
      const blob =
        format === "xlsx"
          ? await exportProgramaXlsx(includedRows)
          : format === "zip"
            ? await exportProgramaZip(includedRows)
            : format === "debug"
              ? await exportProgramaDebugCsv(includedRows)
              : await exportProgramaCsv(includedRows);
      const suffix = format === "xlsx" ? "xlsx" : format === "zip" ? "zip" : "csv";
      const today = new Date().toISOString().slice(0, 10);
      const filename = format === "debug" ? `programa_debug_${today}.${suffix}` : `programa_import_${today}.${suffix}`;
      downloadBlob(blob, filename);
      setWorkflowStage("export");
      setExportGenerated(true);
      setMessage("Use this file for Programa Import Products.");
      setLastSuccessfulStage(`${format.toUpperCase()} export generated`);
      recordDebugTrace({
        action: "programa export",
        stage: "success",
        endpoint,
        message: filename,
      });
    } catch (error) {
      setMessage(formatApiError(error));
      recordDebugTrace({
        action: "programa export",
        stage: "failed",
        endpoint,
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  async function handleSendToPrograma() {
    setBusy("programa");
    setProgramaMessage("");
    setMessage("");
    setLastEndpoint(`${API_BASE || "not configured"}/programa/send`);
    recordDebugTrace({
      action: "send to programa",
      stage: "started",
      endpoint: "/programa/send",
      message: `${includedRows.length} included rows`,
    });
    try {
      const response = await sendToPrograma({
        projectName: project,
        scheduleUrl,
        rows: includedRows,
        allowBlankFields: false,
        uploadProductImages: true,
      });
      setProgramaMessage(response.message || `Programa send status: ${response.status}`);
      setLastSuccessfulStage("Programa send completed");
      recordDebugTrace({
        action: "send to programa",
        stage: "success",
        endpoint: "/programa/send",
        message: response.status,
      });
    } catch (error) {
      setProgramaMessage(formatApiError(error));
      recordDebugTrace({
        action: "send to programa",
        stage: "failed",
        endpoint: "/programa/send",
        ...debugDetailsFromError(error),
      });
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
    setLastEndpoint(`${API_BASE || "not configured"}/vendor-call/script`);
    recordDebugTrace({
      action: "vendor call script",
      stage: "started",
      endpoint: "/vendor-call/script",
      itemId: rowText(vendorCall.row, "Model/SKU") || rowText(vendorCall.row, "Product Name"),
    });
    try {
      const response = await generateVendorCallScript({
        row: vendorCall.row,
        missingFields: vendorCall.missingFields,
        phoneNumber: vendorCall.phoneNumber,
        customGoal: vendorCall.customGoal,
      });
      setVendorCall({ ...vendorCall, script: response.script });
      setLastSuccessfulStage("Vendor call script generated");
      recordDebugTrace({
        action: "vendor call script",
        stage: "success",
        endpoint: "/vendor-call/script",
        itemId: rowText(vendorCall.row, "Model/SKU") || rowText(vendorCall.row, "Product Name"),
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not generate the call script.");
      recordDebugTrace({
        action: "vendor call script",
        stage: "failed",
        endpoint: "/vendor-call/script",
        itemId: rowText(vendorCall.row, "Model/SKU") || rowText(vendorCall.row, "Product Name"),
        ...debugDetailsFromError(error),
      });
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="min-h-screen px-4 py-6 sm:px-7 lg:px-10">
      <div className="mx-auto flex max-w-[1220px] flex-col gap-7">
        <header className="sticky top-0 z-40 flex flex-col gap-4 rounded-2xl border border-linen bg-paper/88 px-4 py-4 shadow-panel backdrop-blur md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <LogoMark />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-orangeBorder bg-orangeSoft px-5 text-sm font-bold text-bronze shadow-sm transition hover:border-bronze hover:bg-white"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings className="h-4 w-4" />
              <span>Settings</span>
            </button>
          </div>
        </header>

        <section className="glass-panel rounded-[28px] p-5 sm:p-7">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-bronze">SCH Catalog Engine</p>
            <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-normal text-charcoal sm:text-5xl">
              Programa Schedule Builder
            </h1>
            {!isSimpleMode ? (
              <p className="mt-4 max-w-2xl text-sm leading-6 text-taupe">
                Convert vendor quotes, spec sheets, product links, and photos into Programa-ready schedules.
              </p>
            ) : null}
          </div>
        </section>

        <StageProgressTracker
          stages={stageSummaries}
          activeIndex={activeMainWorkflowIndex}
          progress={workflowProgress}
          onStageClick={goToMainStage}
        />

        {activeMainWorkflowIndex === 0 ? (
        <Panel step="1" title="Upload" subtitle="Add vendor PDFs, product photos, product links, project context, and default room before moving to parsing." simple={isSimpleMode}>
          <div className="grid gap-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Existing Programa Project / Property">
                <input
                  className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                  value={project}
                  onChange={(event) => setProject(event.target.value)}
                  placeholder="1 Lily Pond Ln"
                />
              </Field>
              <Field label="Default Room / Location">
                <input
                  className="input-surface h-11 w-full rounded-xl px-3 text-sm text-charcoal"
                  value={room}
                  onChange={(event) => setRoom(event.target.value)}
                  placeholder="Kitchen"
                />
              </Field>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <InputCard
                icon={<Upload className="h-5 w-5 text-bronze" />}
                title="Upload PDFs"
                description="Vendor quotes, spec sheets, tear sheets, receipts, or schedules for Step 2 parsing."
                meta={files.length ? `${files.length} PDF${files.length === 1 ? "" : "s"} selected` : "No PDFs selected"}
                simple={isSimpleMode}
              >
                <label className="btn-secondary inline-flex h-10 cursor-pointer items-center justify-center rounded-xl px-4 text-sm font-semibold">
                  Choose PDFs
                  <input
                    ref={fileInputRef}
                    className="hidden"
                    type="file"
                    accept="application/pdf"
                    multiple
                    onChange={(event) => handleFileSelection(event.target.files)}
                  />
                </label>
              </InputCard>

              <InputCard
                icon={<ImageIcon className="h-5 w-5 text-bronze" />}
                title="Upload Product Photos"
                description="Photos are uploaded to Cloudinary during Parse and become image-ready rows."
                meta={bulkImages.length ? `${bulkImages.length} image${bulkImages.length === 1 ? "" : "s"} selected` : "No photos selected"}
                simple={isSimpleMode}
              >
                <label
                  className={`btn-secondary inline-flex h-10 cursor-pointer items-center justify-center rounded-xl px-4 text-sm font-semibold ${
                    isImageDragActive ? "border-orangeBorder bg-orangeSoft" : ""
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
                  Choose Photos
                  <input
                    ref={bulkImageInputRef}
                    className="hidden"
                    type="file"
                    accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                    multiple
                    onChange={(event) => handleBulkImageSelection(event.target.files)}
                  />
                </label>
              </InputCard>

              <InputCard
                icon={<FileText className="h-5 w-5 text-bronze" />}
                title="Paste Product Links"
                description="Product pages, vendor links, or source URLs. One link per line."
                meta={`${urls.split(/\r?\n/).filter((url) => url.trim()).length} link${urls.split(/\r?\n/).filter((url) => url.trim()).length === 1 ? "" : "s"} entered`}
                simple={isSimpleMode}
              >
                <textarea
                  className="input-surface min-h-28 w-full resize-none rounded-xl p-3 text-sm leading-6 text-charcoal"
                  value={urls}
                  onChange={(event) => {
                    setUrls(event.target.value);
                    setWorkflowStage("upload");
                    setParseReviewed(false);
                    setEnrichmentReviewed(false);
                    setExportGenerated(false);
                  }}
                  placeholder={"https://www.vendor.com/product\nhttps://www.vendor.com/quote"}
                />
              </InputCard>
            </div>

            {uploadError || bulkImageError ? (
              <div className="rounded-xl border border-clay/20 bg-clay/10 px-3 py-2 text-sm text-clay">
                {uploadError || bulkImageError}
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

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex items-center gap-2 text-sm text-taupe">
                <input
                  type="checkbox"
                  checked={useAiPdf}
                  onChange={(event) => setUseAiPdf(event.target.checked)}
                  className="h-4 w-4 accent-bronze"
                />
                Use AI to interpret uploaded PDFs
              </label>
              <div className="text-sm text-taupe">
                Input count: <span className="font-semibold text-charcoal">{parseInputCount}</span>
              </div>
            </div>

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
                {!isSimpleMode ? (
                  <p className="text-sm text-taupe">
                    Photo rows are created in Step 2 when you click Parse Files. PDF parsing and web enrichment will not run for photo-only rows.
                  </p>
                ) : null}
                <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                  {bulkImagePreviews.slice(0, 6).map(({ file, url }, index) => (
                    <img key={`${file.name}-${file.size}-${file.lastModified}`} src={url} alt={file.name} className="h-20 w-full rounded-xl object-cover" />
                  ))}
                </div>
              </div>
            ) : null}
            <div className="rounded-2xl border border-linen bg-ivory/70 p-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <div className="text-sm font-semibold text-charcoal">Input Summary</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <StatusBadge value={`${files.length} PDFs`} />
                    <StatusBadge value={`${bulkImages.length} photos`} />
                    <StatusBadge value={`${linkInputCount} product links`} />
                    <StatusBadge value={project.trim() ? `Project: ${project.trim()}` : "No project name"} />
                    <StatusBadge value={room.trim() ? `Default room: ${room.trim()}` : "No default room"} />
                  </div>
                </div>
                <button
                  type="button"
                  className="btn-primary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-6 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                  disabled={!uploadComplete}
                  onClick={continueToParse}
                >
                  {uploadComplete ? <CheckCircle2 className="h-4 w-4" /> : <Upload className="h-4 w-4" />}
                  Continue to Parse
                </button>
              </div>
            </div>
          </div>
        </Panel>
        ) : null}

        {activeMainWorkflowIndex === 1 ? (
        <Panel step="2" title="Parse" subtitle="Create initial product rows from PDFs, product links, and Cloudinary-hosted photo uploads. Enrichment stays off until parse review is complete." simple={isSimpleMode}>
          <div className="grid gap-4">
            <div className="rounded-xl border border-linen bg-ivory/70 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-sm font-semibold text-charcoal">{parseStatus}</div>
                  {!isSimpleMode ? (
                    <div className="mt-1 text-xs text-taupe">Endpoint: {lastEndpoint || `${API_BASE || "not configured"}/intake/generate`}</div>
                  ) : null}
                </div>
                <button
                  className="btn-primary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-6 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
                  disabled={busy === "generate" || parseInputCount === 0}
                  onClick={handleGenerate}
                >
                  {busy === "generate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                  Parse Files
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <StatusBadge value={`${files.length} PDFs`} />
                <StatusBadge value={`${bulkImages.length} photos`} />
                <StatusBadge value={`${linkInputCount} URLs`} />
                <StatusBadge value={useAiPdf ? "AI PDF parsing on" : "AI PDF parsing off"} />
                <StatusBadge value={`${includedRows.length} products found`} />
              </div>
            </div>
            {message ? <p className="whitespace-pre-wrap rounded-xl border border-linen bg-white px-4 py-3 text-sm text-charcoal/70">{message}</p> : null}
            {errors.length ? <ErrorList errors={errors} debugMode={debugMode} /> : null}
          </div>
        </Panel>
        ) : null}

        {debugMode ? (
          <DebugTracePanel
            traces={debugTraces}
            lastSuccessfulStage={lastSuccessfulStage}
            copyStatus={debugCopyStatus}
            onCopy={copyDebugReport}
          />
        ) : null}

        {activeMainWorkflowIndex === 1 && parsedReviewReady ? (
          <Panel step="3" title="Review Parsed Data" subtitle="Start with the summary, then expand the parsed product table only when needed." simple={isSimpleMode}>
            <div className="grid gap-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <SummaryCard label="Products found" value={includedRows.length} />
                <SummaryCard label="Missing SKU" value={missingSkuCount} tone={missingSkuCount ? "warning" : "ok"} />
                <SummaryCard label="Missing dimensions" value={missingDimensionsCount} tone={missingDimensionsCount ? "warning" : "ok"} />
                <SummaryCard label="Missing image" value={missingImageCount} tone={missingImageCount ? "warning" : "ok"} />
                <SummaryCard label="Missing supplier" value={missingSupplierCount} tone={missingSupplierCount ? "warning" : "ok"} />
                <SummaryCard label="Room assignments" value={rowsWithRoomCount} />
                <SummaryCard label="Manual review" value={needsReview + ignored} tone={needsReview + ignored ? "warning" : "ok"} />
                <SummaryCard label="Duplicates removed" value={exportSummary.duplicate_rows_removed} tone={exportSummary.duplicate_rows_removed ? "warning" : "ok"} />
              </div>
              {exportSummary.blank_price_only_rows.length ||
              exportSummary.missing_model_manufacturer.length ||
              exportSummary.phone_email_header_contamination.length ||
              exportSummary.duplicate_rows_removed ? (
                <div className="rounded-xl border border-orangeBorder bg-orangeSoft/40 px-4 py-3 text-sm text-bronze">
                  <div className="font-semibold text-charcoal">Parse QA warnings</div>
                  {exportSummary.duplicate_rows_removed ? <div>{exportSummary.duplicate_rows_removed} duplicate candidate row(s) detected.</div> : null}
                  {exportSummary.blank_price_only_rows.length ? <div>{exportSummary.blank_price_only_rows.length} price-only/manual review row(s) detected.</div> : null}
                  {exportSummary.missing_model_manufacturer.length ? <div>{exportSummary.missing_model_manufacturer.length} row(s) missing manufacturer/model.</div> : null}
                  {exportSummary.phone_email_header_contamination.length ? <div>{exportSummary.phone_email_header_contamination.length} row(s) flagged for header/contact contamination.</div> : null}
                </div>
              ) : (
                <div className="rounded-xl border border-sage/20 bg-sage/10 px-4 py-3 text-sm font-semibold text-sage">
                  Parse QA clear: no blocking duplicate, identity, or contamination warnings.
                </div>
              )}
              <DisclosureButton open={parsedProductsOpen} onClick={() => setParsedProductsOpen((open) => !open)}>
                View parsed products
              </DisclosureButton>
              {parsedProductsOpen ? (
                <ProductTable rows={rows} categories={categories} sections={sections} updateRow={updateRow} openVendorCall={openVendorCall} debugMode={debugMode} />
              ) : null}
              <div className="flex flex-col gap-3 rounded-2xl border border-linen bg-ivory/70 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-sm font-semibold text-charcoal">Parse Complete</div>
                  <p className="mt-1 text-sm text-taupe">{includedRows.length} product{includedRows.length === 1 ? "" : "s"} detected. Review or edit rows, then continue.</p>
                </div>
                <button
                  type="button"
                  className="btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                  disabled={!hasParsedRows || errors.some((error) => error.toLowerCase().includes("parse failed"))}
                  onClick={continueToEnrich}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Continue to Enrich
                </button>
              </div>
            </div>
          </Panel>
        ) : null}

        {activeMainWorkflowIndex === 2 && (parseReviewed || enrichmentHasRun) ? (
          <Panel step="4" title="Enrich" subtitle="Run missing-field enrichment only after parsed data has been reviewed. This does not reparse the PDFs." simple={isSimpleMode}>
            <div className="grid gap-4">
              <div className="rounded-xl border border-linen bg-ivory/70 p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <div className="text-sm font-semibold text-charcoal">{enrichmentStatus}</div>
                    <div className="mt-1 text-xs text-taupe">Estimated cost: {estimatedCost}</div>
                  </div>
                  <button
                    className="btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                    disabled={(busy !== "" && busy !== "validate") || busy === "validate" || rows.length === 0}
                    onClick={handleValidate}
                  >
                    {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    Enrich Missing Data
                  </button>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <StatusBadge value={`${includedRows.length} products queued`} />
                  <StatusBadge value={`Mode: ${enrichmentMode}`} />
                  <StatusBadge value={`Budget cap: ${formatUsd(enrichmentBudgetUsd)}`} />
                  <StatusBadge value={`${missingSkuCount} missing SKU/model`} />
                  <StatusBadge value={`${missingDimensionsCount} missing dimensions`} />
                  <StatusBadge value={`${missingImageCount} missing image`} />
                  <StatusBadge value={useWebEnrichment ? "Web enrichment on" : "Web enrichment off"} />
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={(busy !== "" && busy !== "validate") || busy === "validate" || rows.length === 0 || missingDimensionsCount === 0}
                    onClick={() => handleTargetedRetry("Dimension-focused retry", "fast", false)}
                  >
                    {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                    Dimension-focused retry
                  </button>
                  <button
                    type="button"
                    className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={(busy !== "" && busy !== "validate") || busy === "validate" || rows.length === 0 || (missingDimensionsCount === 0 && missingImageCount === 0)}
                    onClick={() => handleTargetedRetry("Manufacturer-only retry", "fast", false)}
                  >
                    {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    Manufacturer-only retry
                  </button>
                  <button
                    type="button"
                    className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={(busy !== "" && busy !== "validate") || busy === "validate" || rows.length === 0 || missingDimensionsCount === 0}
                    onClick={() => handleTargetedRetry("Spec sheet search", "standard", false)}
                  >
                    {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                    Spec sheet search
                  </button>
                  <button
                    type="button"
                    className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={(busy !== "" && busy !== "imageRecovery") || busy === "imageRecovery" || rows.length === 0 || missingImageCount === 0}
                    onClick={handleRecoverMissingImages}
                  >
                    {busy === "imageRecovery" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImageIcon className="h-4 w-4" />}
                    Recover Missing Images
                  </button>
                  <button
                    type="button"
                    className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={
                      (busy !== "" && busy !== "furtherEnrichment") ||
                      busy === "furtherEnrichment" ||
                      rows.length === 0 ||
                      !enrichmentHasRun ||
                      !furtherEnrichmentEnabled ||
                      furtherEnrichmentCandidateCount === 0
                    }
                    onClick={handleFurtherEnrichment}
                    title={
                      furtherEnrichmentEnabled
                        ? `Estimated ${formatUsd(furtherEnrichmentEstimatedCost, 4)} for ${furtherEnrichmentCandidateCount} incomplete row${furtherEnrichmentCandidateCount === 1 ? "" : "s"}`
                        : "Enable Further Enrichment in Settings."
                    }
                  >
                    {busy === "furtherEnrichment" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    Further Enrich Missing Fields
                  </button>
                </div>
                <div className="mt-3 rounded-xl border border-linen bg-white/75 px-3 py-2 text-xs text-taupe">
                  Further Enrichment: {furtherEnrichmentEnabled ? "enabled" : "off"} · {furtherEnrichmentCandidateCount} incomplete row{furtherEnrichmentCandidateCount === 1 ? "" : "s"} · estimated {formatUsd(furtherEnrichmentEstimatedCost, 4)} · cap {formatUsd(furtherEnrichmentBudgetUsd)}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryCard label="Rows to inspect" value={includedRows.length} />
                <SummaryCard label="Need enrichment" value={unresolvedCount} tone={unresolvedCount ? "warning" : "ok"} />
                <SummaryCard label="Images present" value={imagesFoundCount} />
                <SummaryCard label="Dimensions present" value={dimensionsFoundCount} />
                <SummaryCard label="Manufacturer set" value={rowsWithBrandCount} />
                <SummaryCard label="Source URLs" value={rowsWithSourceUrlCount} />
              </div>
              {!isSimpleMode ? (
                <p className="rounded-xl border border-linen bg-white px-4 py-3 text-sm text-charcoal/70">
                  Enrichment uses existing SKU/model, product URLs, preferred sources, and cache first. It fills missing data without rerunning PDF parsing.
                </p>
              ) : null}
              {errors.length ? <ErrorList errors={errors} debugMode={debugMode} /> : null}
            </div>
          </Panel>
        ) : null}

        {activeMainWorkflowIndex === 2 && enrichmentHasRun ? (
          <Panel step="5" title="Review Enriched Data" subtitle="Confirm readiness after enrichment before exporting." simple={isSimpleMode}>
            <div className="grid gap-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryCard label="Rows enriched" value={enrichmentStats.rowsEnriched} />
                <SummaryCard label="Products ready" value={readyRows} tone={readyRows ? "ok" : "neutral"} />
                <SummaryCard label="Needs review" value={needsReview} tone={needsReview ? "warning" : "ok"} />
                <SummaryCard label="Images found" value={imagesFoundCount} />
                <SummaryCard label="Dimensions found" value={dimensionsFoundCount} />
                <SummaryCard label="Missing images" value={enrichmentStats.missingImages} tone={enrichmentStats.missingImages ? "warning" : "ok"} />
                <SummaryCard label="Missing dimensions" value={enrichmentStats.missingDimensions} tone={enrichmentStats.missingDimensions ? "warning" : "ok"} />
                <SummaryCard label="Avg confidence" value={enrichmentStats.averageConfidence} />
                <SummaryCard label="Est. cost" value={estimatedCost} />
                <SummaryCard label="Budget cap" value={enrichmentStats.budgetCap} />
                <SummaryCard label="Unique products searched" value={enrichmentStats.uniqueProductsSearched} />
                <SummaryCard label="Cost/useful field" value={enrichmentStats.costPerUsefulField} />
                <SummaryCard label="Cost/dimension" value={enrichmentStats.costPerDimensionFound} />
                <SummaryCard label="Dimensions per $" value={enrichmentStats.dimensionsFoundPerDollar} />
                <SummaryCard label="Images per $" value={enrichmentStats.imagesFoundPerDollar} />
                <SummaryCard label="Cache hits" value={enrichmentStats.cacheHits} />
                <SummaryCard label="Duplicate models skipped" value={enrichmentStats.duplicateModelsSkipped} />
                <SummaryCard label="Searches avoided" value={enrichmentStats.searchesAvoided} />
                <SummaryCard label="Ignored" value={ignored} />
              </div>
              {debugMode && enrichmentDebugReport ? (
                <EnrichmentDebugReportPanel
                  report={enrichmentDebugReport}
                  status={enrichmentDebugReportStatus}
                  onCopy={copyEnrichmentDebugReport}
                  onDownload={downloadEnrichmentDebugReport}
                />
              ) : null}
              <DisclosureButton open={enrichedProductsOpen} onClick={() => setEnrichedProductsOpen((open) => !open)}>
                View enriched products
              </DisclosureButton>
              {enrichedProductsOpen ? (
                <ProductTable rows={rows} categories={categories} sections={sections} updateRow={updateRow} openVendorCall={openVendorCall} debugMode={debugMode} />
              ) : null}
              <div className="flex flex-col gap-3 rounded-2xl border border-linen bg-ivory/70 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-sm font-semibold text-charcoal">
                    {enrichmentStats.missingDimensions || enrichmentStats.missingImages ? "Partially Enriched" : "Enrichment Complete"}
                  </div>
                  <p className="mt-1 text-sm text-taupe">{Math.max(0, includedRows.length - unresolvedCount)}/{includedRows.length} product{includedRows.length === 1 ? "" : "s"} enriched or intentionally left for review.</p>
                </div>
                <button
                  type="button"
                  className="btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold"
                  onClick={continueToExport}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Continue to Export
                </button>
              </div>
            </div>
          </Panel>
        ) : null}

        {activeMainWorkflowIndex === 3 && (enrichmentReviewed || exportGenerated) ? (
          <Panel step="6" title="Export" subtitle="Download the Programa-ready workbook first. CSV, ZIP, debug, and direct send are secondary." simple={isSimpleMode}>
            <div className="grid gap-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryCard label="Programa readiness" value={`${readinessScore}%`} tone={readinessScore >= 90 ? "ok" : readinessScore ? "warning" : "neutral"} />
                <SummaryCard label="Export-ready rows" value={exportSummary.export_count} tone={exportSummary.export_count ? "ok" : "warning"} />
                <SummaryCard label="Missing fields" value={missingInputRows.length} tone={missingInputRows.length ? "warning" : "ok"} />
                <SummaryCard label="Images ready" value={`${exportSummary.image_url_present}/${exportSummary.image_url_total}`} />
              </div>
              <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr]">
                <button
                  className="btn-primary inline-flex h-14 items-center justify-center gap-2 rounded-xl px-6 text-base font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                  disabled={busy === "export" || exportSummary.export_count === 0}
                  onClick={() => handleProgramaExport("xlsx")}
                >
                  {busy === "export" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                  Download Excel for Programa
                </button>
                <div className="grid gap-2 sm:grid-cols-2">
                  <button
                    className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={busy === "export" || exportSummary.export_count === 0}
                    onClick={() => handleProgramaExport("csv")}
                  >
                    <Download className="h-4 w-4" />
                    Download CSV
                  </button>
                  <button
                    className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={busy === "export" || exportSummary.export_count === 0}
                    onClick={() => handleProgramaExport("zip")}
                  >
                    <Archive className="h-4 w-4" />
                    Download ZIP with Images
                  </button>
                  <button
                    className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={busy === "export" || rows.length === 0}
                    onClick={handleJsonExport}
                  >
                    <FileText className="h-4 w-4" />
                    Download JSON
                  </button>
                  <button
                    className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                    disabled={busy === "export" || rows.length === 0}
                    onClick={() => handleProgramaExport("debug")}
                  >
                    <FileText className="h-4 w-4" />
                    Export Debug Report
                  </button>
                  {programaSendEnabled ? (
                    <button
                      className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold text-taupe hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                      disabled={busy === "programa" || exportSummary.export_count === 0 || !scheduleUrl.trim()}
                      onClick={handleSendToPrograma}
                      title="Send approved rows to Programa"
                    >
                      {busy === "programa" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                      Send to Programa
                    </button>
                  ) : null}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusBadge value={`${exportSummary.export_count} export-ready`} />
                <StatusBadge value={`Images ${exportSummary.image_url_present}/${exportSummary.image_url_total}`} />
                <StatusBadge value={`${exportSummary.missing_section.length} missing section`} />
                {exportSummary.parsed_rows_count ? (
                  <StatusBadge value={`${exportSummary.parsed_rows_count} parsed · ${exportSummary.export_rows_count || exportSummary.export_count} export rows`} />
                ) : null}
                {programaSendEnabled ? <StatusBadge value="Programa send configured" /> : <StatusBadge value="Programa send not configured" />}
              </div>
              {programaMessage ? (
                <p className="whitespace-pre-wrap rounded-xl border border-linen bg-white px-4 py-3 text-sm text-charcoal/70">{programaMessage}</p>
              ) : null}
              {exportSummary.missing_image_url ||
              exportSummary.missing_dimensions ||
              exportSummary.rejected_product_urls.length ||
              exportSummary.pdf_product_urls.length ||
              exportSummary.suspicious_dimensions_rejected.length ||
              exportSummary.duplicate_rows_removed ||
              exportSummary.blank_price_only_rows?.length ||
              exportSummary.missing_model_manufacturer?.length ||
              exportSummary.phone_email_header_contamination?.length ? (
                <div className="rounded-xl border border-orangeBorder bg-orangeSoft/40 px-4 py-3 text-sm text-bronze">
                  {exportSummary.duplicate_rows_removed ? (
                    <div>{exportSummary.duplicate_rows_removed} duplicate raw/fallback row(s) removed before export.</div>
                  ) : null}
                  {exportSummary.missing_image_url ? <div>{exportSummary.missing_image_url} row(s) missing image URLs.</div> : null}
                  {exportSummary.missing_dimensions ? <div>{exportSummary.missing_dimensions} row(s) missing dimensions.</div> : null}
                  {exportSummary.blank_price_only_rows?.length ? (
                    <div>{exportSummary.blank_price_only_rows.length} blank/price-only quote row(s) held for manual review.</div>
                  ) : null}
                  {exportSummary.missing_model_manufacturer?.length ? (
                    <div>{exportSummary.missing_model_manufacturer.length} row(s) missing manufacturer/model identity.</div>
                  ) : null}
                  {exportSummary.phone_email_header_contamination?.length ? (
                    <div>{exportSummary.phone_email_header_contamination.length} row(s) flagged for phone/email/header contamination.</div>
                  ) : null}
                  {exportSummary.suspicious_dimensions_rejected.length ? (
                    <div>{exportSummary.suspicious_dimensions_rejected.length} suspicious dimension value(s) rejected before export.</div>
                  ) : null}
                  {exportSummary.rejected_product_urls.length ? (
                    <div>{exportSummary.rejected_product_urls.length} sitemap/category/search Product URL(s) rejected.</div>
                  ) : null}
                  {exportSummary.pdf_product_urls.length ? (
                    <div>{exportSummary.pdf_product_urls.length} row(s) still use a PDF/spec sheet as Product URL.</div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </Panel>
        ) : null}
        {settingsOpen ? (
          <SettingsDialog
            buildInfo={buildInfo}
            apiStatus={apiConnectionStatus}
            apiStatusText={apiConnectionText}
            apiBase={displayApiBase}
            rawApiBase={RAW_API_BASE || "not configured"}
            lastEndpoint={lastEndpoint}
            estimatedCost={estimatedCost}
            integrationsStatus={integrationsStatus}
            useAiPdf={useAiPdf}
            useWebEnrichment={useWebEnrichment}
            enrichmentMode={enrichmentMode}
            forceRefreshEnrichment={forceRefreshEnrichment}
            enrichmentBudgetUsd={enrichmentBudgetUsd}
            furtherEnrichmentEnabled={furtherEnrichmentEnabled}
            furtherEnrichmentBudgetUsd={furtherEnrichmentBudgetUsd}
            furtherEnrichmentCandidateCount={furtherEnrichmentCandidateCount}
            furtherEnrichmentEstimatedCost={furtherEnrichmentEstimatedCost}
            debugMode={debugMode}
            accentThemeId={accentThemeId}
            uiMode={uiMode}
            scheduleUrl={scheduleUrl}
            bulkImagesCount={bulkImages.length}
            photoBulkSummary={photoBulkSummary}
            programaSendEnabled={programaSendEnabled}
            exportReadyCount={exportSummary.export_count}
            imageReadyCount={exportSummary.image_url_present}
            imageTotalCount={exportSummary.image_url_total}
            sourceMemoryStatus={sourceMemoryStatus}
            sourceMemoryBackend={sourceMemoryBackend}
            sourceMemoryAudit={sourceMemoryAudit}
            storedSources={storedSources}
            preferredDomains={preferredDomains}
            sourceSearch={sourceSearch}
            domainSearch={domainSearch}
            sourceTypeFilter={sourceTypeFilter}
            domainTypeFilter={domainTypeFilter}
            sourceForm={sourceForm}
            domainForm={domainForm}
            editingSourceId={editingSourceId}
            editingDomainId={editingDomainId}
            onUseAiPdfChange={setUseAiPdf}
            onUseWebEnrichmentChange={setUseWebEnrichment}
            onEnrichmentModeChange={setEnrichmentMode}
            onForceRefreshEnrichmentChange={setForceRefreshEnrichment}
            onEnrichmentBudgetUsdChange={setEnrichmentBudgetUsd}
            onFurtherEnrichmentEnabledChange={setFurtherEnrichmentEnabled}
            onFurtherEnrichmentBudgetUsdChange={setFurtherEnrichmentBudgetUsd}
            onDebugModeChange={setDebugMode}
            themePreference={themePreference}
            onThemePreferenceChange={setThemePreference}
            onAccentThemeChange={setAccentThemeId}
            onUiModeChange={setUiMode}
            onScheduleUrlChange={setScheduleUrl}
            onSourceSearchChange={setSourceSearch}
            onDomainSearchChange={setDomainSearch}
            onSourceTypeFilterChange={setSourceTypeFilter}
            onDomainTypeFilterChange={setDomainTypeFilter}
            onSourceFormChange={setSourceForm}
            onDomainFormChange={setDomainForm}
            onSaveSource={handleSaveSource}
            onEditSource={handleEditSource}
            onDeleteSource={handleDeleteSource}
            onReverifySource={handleReverifySource}
            onCancelSourceEdit={() => {
              setEditingSourceId("");
              setSourceForm(emptySourceForm);
            }}
            onSaveDomain={handleSaveDomain}
            onEditDomain={handleEditDomain}
            onDeleteDomain={handleDeleteDomain}
            onCancelDomainEdit={() => {
              setEditingDomainId("");
              setDomainForm(emptyDomainForm);
            }}
            onClose={() => setSettingsOpen(false)}
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

function InputCard({
  icon,
  title,
  description,
  meta,
  simple = false,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  meta: string;
  simple?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="grid gap-4 rounded-2xl border border-linen bg-white/78 p-4">
      <div className="flex items-start gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-xl border border-orangeBorder bg-orangeSoft">
          {icon}
        </div>
        <div>
          <h3 className="text-sm font-semibold text-charcoal">{title}</h3>
          {!simple ? <p className="mt-1 text-sm leading-5 text-taupe">{description}</p> : null}
          <p className="mt-2 text-xs font-semibold uppercase tracking-[0.08em] text-bronze">{meta}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function SummaryCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  tone?: "neutral" | "ok" | "warning";
}) {
  const toneClass =
    tone === "ok"
      ? "border-sage/20 bg-sage/10 text-sage"
      : tone === "warning"
        ? "border-orangeBorder bg-orangeSoft text-bronze"
        : "border-linen bg-white text-charcoal";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
      <div className="text-2xl font-semibold leading-none">{value}</div>
      <div className="mt-2 text-xs font-semibold uppercase tracking-[0.08em] opacity-75">{label}</div>
    </div>
  );
}

function DisclosureButton({
  open,
  onClick,
  children,
}: {
  open: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="btn-secondary inline-flex h-11 w-fit items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold hover:bg-ivory"
      onClick={onClick}
    >
      <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} />
      {children}
    </button>
  );
}

function ErrorList({ errors, debugMode }: { errors: string[]; debugMode: boolean }) {
  const shownErrors = displayErrorsForMode(errors, debugMode);
  if (!shownErrors.length) return null;
  return (
    <div className="rounded-xl border border-clay/20 bg-clay/10 px-4 py-3 text-sm text-clay">
      {shownErrors.map((error) => (
        <div key={error}>{error}</div>
      ))}
    </div>
  );
}

function EnrichmentDebugReportPanel({
  report,
  status,
  onCopy,
  onDownload,
}: {
  report: string;
  status: string;
  onCopy: () => void;
  onDownload: () => void;
}) {
  return (
    <section className="rounded-2xl border border-linen bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-base font-semibold text-charcoal">Copy Debug Report for ChatGPT</h3>
          <p className="mt-1 text-sm text-taupe">
            Plain-text enrichment trace with parse diagnostics, source attempts, costs, errors, and suggested next steps.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold"
            onClick={onCopy}
          >
            Copy Debug Report
          </button>
          <button
            type="button"
            className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold"
            onClick={onDownload}
          >
            Download .txt
          </button>
        </div>
      </div>
      {status ? <p className="mt-3 text-sm font-semibold text-bronze">{status}</p> : null}
      <textarea
        className="mt-4 h-80 w-full resize-y rounded-xl border border-linen bg-ivory/70 p-3 font-mono text-xs leading-5 text-charcoal outline-none focus:border-orangeBorder focus:ring-2 focus:ring-orangeSoft"
        value={report}
        readOnly
        aria-label="Debug enrichment report"
      />
    </section>
  );
}

function ProductTable({
  rows,
  categories,
  sections,
  updateRow,
  openVendorCall,
  debugMode,
}: {
  rows: IntakeRow[];
  categories: string[];
  sections: string[];
  updateRow: (index: number, key: string, value: unknown) => void;
  openVendorCall: (row: IntakeRow, missingFields: string[]) => void;
  debugMode: boolean;
}) {
  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-linen bg-white/60 px-5 py-10 text-center">
        <p className="text-sm text-taupe">Parsed products will appear here.</p>
      </div>
    );
  }

  return (
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
            <Fragment key={index}>
              <tr className="align-top transition-colors hover:bg-orangeSoft/30">
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
              {debugMode ? (
                <tr>
                  <td colSpan={reviewColumns.length} className="border-b border-linen/70 bg-ivory/60 px-3 py-2">
                    <RowDebugTrace row={row} />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RowDebugTrace({ row }: { row: IntakeRow }) {
  const fields = [
    ["Query", rowText(row, "search_query_used")],
    ["Provider", rowText(row, "search_provider")],
    ["Candidates", rowText(row, "product_url_candidates")],
    ["Chosen URL", rowText(row, "selected_product_url") || rowText(row, "Product URL")],
    ["URL confidence", rowText(row, "selected_product_url_confidence") || rowText(row, "product_url_confidence")],
    ["Dimensions", rowText(row, "dimensions_extracted") || rowText(row, "Dimensions")],
    ["Dimension source", rowText(row, "dimension_source_url") || rowText(row, "Dimension Source URL")],
    ["Image", rowText(row, "selected_image_url") || rowText(row, "Image URL")],
    ["Image candidates", rowText(row, "image_candidate_diagnostics")],
    ["Reason", rowText(row, "skipped_reason") || rowText(row, "Suggested Action")],
  ].filter(([, value]) => value);

  if (!fields.length) {
    return <span className="text-xs text-taupe">No enrichment debug trace recorded for this row.</span>;
  }

  return (
    <details className="group rounded-xl border border-linen bg-white/80 px-3 py-2 text-xs">
      <summary className="cursor-pointer list-none font-semibold text-charcoal">
        Item debug trace
        <span className="ml-2 text-taupe group-open:hidden">Show details</span>
        <span className="ml-2 hidden text-taupe group-open:inline">Hide details</span>
      </summary>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-linen bg-ivory/70 p-2">
            <div className="font-semibold text-charcoal">{label}</div>
            <div className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-taupe">
              {value}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
}

function ReviewValue({ value }: { value: string }) {
  return value ? <span className="text-charcoal">{value}</span> : <span className="font-semibold text-clay">Missing</span>;
}

function DebugTracePanel({
  traces,
  lastSuccessfulStage,
  copyStatus,
  onCopy,
}: {
  traces: DebugTrace[];
  lastSuccessfulStage: string;
  copyStatus: string;
  onCopy: () => void;
}) {
  const latestTraces = traces.slice(-6).reverse();
  return (
    <section className="rounded-2xl border border-orangeBorder bg-orangeSoft/45 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-bronze">Debug Mode</h2>
          <p className="mt-1 text-sm text-charcoal/70">Last successful stage: {lastSuccessfulStage}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-secondary inline-flex h-9 items-center justify-center rounded-xl px-3 text-xs font-semibold hover:bg-white"
            onClick={onCopy}
          >
            Copy debug report
          </button>
          {copyStatus ? <span className="text-xs font-semibold text-bronze">{copyStatus}</span> : null}
        </div>
      </div>
      <div className="mt-3 grid gap-2">
        {latestTraces.length ? (
          latestTraces.map((trace, index) => (
            <div key={`${trace.timestamp}-${index}`} className="rounded-xl border border-linen bg-white/82 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-charcoal">{trace.action}</span>
                <span className="font-mono text-taupe">{new Date(trace.timestamp).toLocaleTimeString()}</span>
              </div>
              <div className="mt-2 grid gap-1 text-taupe sm:grid-cols-2">
                <span>Stage: {trace.stage}</span>
                <span>Endpoint: {trace.endpoint || "none"}</span>
                {trace.statusCode ? <span>Status: {trace.statusCode}</span> : null}
                {trace.itemId ? <span>Item: {trace.itemId}</span> : null}
                {trace.fallback ? <span>Fallback: {trace.fallback}</span> : null}
              </div>
              {trace.message ? <p className="mt-2 whitespace-pre-wrap text-charcoal/70">{trace.message}</p> : null}
            </div>
          ))
        ) : (
          <p className="rounded-xl border border-linen bg-white/82 p-3 text-sm text-taupe">No user action traces yet.</p>
        )}
      </div>
    </section>
  );
}

function SettingsDialog({
  buildInfo,
  apiStatus,
  apiStatusText,
  apiBase,
  rawApiBase,
  lastEndpoint,
  estimatedCost,
  integrationsStatus,
  useAiPdf,
  useWebEnrichment,
  enrichmentMode,
  forceRefreshEnrichment,
  enrichmentBudgetUsd,
  furtherEnrichmentEnabled,
  furtherEnrichmentBudgetUsd,
  furtherEnrichmentCandidateCount,
  furtherEnrichmentEstimatedCost,
  debugMode,
  accentThemeId,
  uiMode,
  scheduleUrl,
  bulkImagesCount,
  photoBulkSummary,
  programaSendEnabled,
  exportReadyCount,
  imageReadyCount,
  imageTotalCount,
  sourceMemoryStatus,
  sourceMemoryBackend,
  sourceMemoryAudit,
  storedSources,
  preferredDomains,
  sourceSearch,
  domainSearch,
  sourceTypeFilter,
  domainTypeFilter,
  sourceForm,
  domainForm,
  editingSourceId,
  editingDomainId,
  onUseAiPdfChange,
  onUseWebEnrichmentChange,
  onEnrichmentModeChange,
  onForceRefreshEnrichmentChange,
  onEnrichmentBudgetUsdChange,
  onFurtherEnrichmentEnabledChange,
  onFurtherEnrichmentBudgetUsdChange,
  onDebugModeChange,
  themePreference,
  onThemePreferenceChange,
  onAccentThemeChange,
  onUiModeChange,
  onScheduleUrlChange,
  onSourceSearchChange,
  onDomainSearchChange,
  onSourceTypeFilterChange,
  onDomainTypeFilterChange,
  onSourceFormChange,
  onDomainFormChange,
  onSaveSource,
  onEditSource,
  onDeleteSource,
  onReverifySource,
  onCancelSourceEdit,
  onSaveDomain,
  onEditDomain,
  onDeleteDomain,
  onCancelDomainEdit,
  onClose,
}: {
  buildInfo: BuildInfo;
  apiStatus: "checking" | "online" | "offline" | "misconfigured";
  apiStatusText: string;
  apiBase: string;
  rawApiBase: string;
  lastEndpoint: string;
  estimatedCost: string;
  integrationsStatus: IntegrationsStatus;
  useAiPdf: boolean;
  useWebEnrichment: boolean;
  enrichmentMode: EnrichmentMode;
  forceRefreshEnrichment: boolean;
  enrichmentBudgetUsd: number;
  furtherEnrichmentEnabled: boolean;
  furtherEnrichmentBudgetUsd: number;
  furtherEnrichmentCandidateCount: number;
  furtherEnrichmentEstimatedCost: number;
  debugMode: boolean;
  accentThemeId: AccentThemeId;
  uiMode: UiMode;
  scheduleUrl: string;
  bulkImagesCount: number;
  photoBulkSummary: { success: number; failed: number };
  programaSendEnabled: boolean;
  exportReadyCount: number;
  imageReadyCount: number;
  imageTotalCount: number;
  sourceMemoryStatus: string;
  sourceMemoryBackend: string;
  sourceMemoryAudit: {
    runtime_cache_persistent?: boolean;
    runtime_cache_persistence_note?: string;
    product_enrichment_cache_path?: string;
    product_knowledge_base_backend?: string;
    tables?: string[];
  };
  storedSources: StoredProductSource[];
  preferredDomains: PreferredSourceDomain[];
  sourceSearch: string;
  domainSearch: string;
  sourceTypeFilter: SourceTypeFilter;
  domainTypeFilter: SourceTypeFilter;
  sourceForm: SourceForm;
  domainForm: DomainForm;
  editingSourceId: string;
  editingDomainId: string;
  onUseAiPdfChange: (value: boolean) => void;
  onUseWebEnrichmentChange: (value: boolean) => void;
  onEnrichmentModeChange: (value: EnrichmentMode) => void;
  onForceRefreshEnrichmentChange: (value: boolean) => void;
  onEnrichmentBudgetUsdChange: (value: number) => void;
  onFurtherEnrichmentEnabledChange: (value: boolean) => void;
  onFurtherEnrichmentBudgetUsdChange: (value: number) => void;
  onDebugModeChange: (value: boolean) => void;
  themePreference: ThemePreference;
  onThemePreferenceChange: (value: ThemePreference) => void;
  onAccentThemeChange: (value: AccentThemeId) => void;
  onUiModeChange: (value: UiMode) => void;
  onScheduleUrlChange: (value: string) => void;
  onSourceSearchChange: (value: string) => void;
  onDomainSearchChange: (value: string) => void;
  onSourceTypeFilterChange: (value: SourceTypeFilter) => void;
  onDomainTypeFilterChange: (value: SourceTypeFilter) => void;
  onSourceFormChange: (value: SourceForm | ((current: SourceForm) => SourceForm)) => void;
  onDomainFormChange: (value: DomainForm | ((current: DomainForm) => DomainForm)) => void;
  onSaveSource: () => void;
  onEditSource: (source: StoredProductSource) => void;
  onDeleteSource: (source: StoredProductSource) => void;
  onReverifySource: (source: StoredProductSource) => void;
  onCancelSourceEdit: () => void;
  onSaveDomain: () => void;
  onEditDomain: (domain: PreferredSourceDomain) => void;
  onDeleteDomain: (domain: PreferredSourceDomain) => void;
  onCancelDomainEdit: () => void;
  onClose: () => void;
}) {
  const statusLabel =
    apiStatus === "online"
      ? "Connected"
      : apiStatus === "misconfigured"
        ? "Misconfigured"
        : apiStatus === "offline"
          ? "Offline"
          : "Checking";
  const isSimpleMode = uiMode === "simple";
  const openAiConfigured = integrationsStatus.openai?.configured === true;
  const openAiStatusLabel = integrationsStatus.openai?.status || (openAiConfigured ? "Connected" : "Not Configured");
  const openAiModel = integrationsStatus.openai?.model || "Not reported";

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end bg-charcoal/28 px-4 py-5 backdrop-blur-sm sm:px-7">
      <div className="max-h-[calc(100vh-2.5rem)] w-full max-w-2xl origin-top-right overflow-y-auto rounded-2xl border border-linen bg-white p-5 shadow-xl">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-charcoal">Settings</h2>
            {!isSimpleMode ? (
              <p className="mt-1 text-sm text-taupe">Workflow preferences for parsing, enrichment, appearance, and export.</p>
            ) : null}
          </div>
          <button
            type="button"
            className="btn-secondary inline-flex h-9 w-9 items-center justify-center rounded-xl"
            onClick={onClose}
            aria-label="Close settings"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {!debugMode && (apiStatus === "offline" || apiStatus === "misconfigured") ? (
          <div className="mt-5 rounded-xl border border-clay/20 bg-clay/10 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-charcoal">Backend Connection Issue</h3>
                <p className="mt-1 text-sm text-taupe">{apiStatusText}</p>
              </div>
              <SettingsStatusPill status={apiStatus} label={statusLabel} />
            </div>
          </div>
        ) : null}

        <div className="mt-4 rounded-xl border border-linen bg-white p-4">
          <h3 className="text-sm font-semibold text-charcoal">Appearance</h3>
          {!isSimpleMode ? (
            <p className="mt-1 text-sm text-taupe">Choose the workspace theme and SCH accent styling.</p>
          ) : null}
          <div className="mt-4">
            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-charcoal/55">Theme</div>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {themeOptions.map((theme) => {
                const selected = themePreference === theme.id;
                return (
                  <button
                    key={theme.id}
                    type="button"
                    className={`min-h-12 rounded-xl border px-3 text-left text-sm font-semibold transition ${
                      selected ? "border-orangeBorder bg-orangeSoft text-bronze" : "border-linen bg-white text-charcoal hover:border-orangeBorder"
                    }`}
                    onClick={() => onThemePreferenceChange(theme.id)}
                    aria-pressed={selected}
                  >
                    {theme.label}
                    {!isSimpleMode ? (
                      <span className="mt-1 block text-xs font-medium text-taupe">{theme.description}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mt-4">
            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-charcoal/55">Accent</div>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {accentThemes.map((theme) => {
                const selected = theme.id === accentThemeId;
                return (
                  <button
                    key={theme.id}
                    type="button"
                    className={`flex min-h-11 items-center gap-2 rounded-xl border px-3 text-left text-sm font-semibold transition ${
                      selected ? "border-orangeBorder bg-orangeSoft text-bronze" : "border-linen bg-white text-charcoal hover:border-orangeBorder"
                    }`}
                    onClick={() => onAccentThemeChange(theme.id)}
                    aria-pressed={selected}
                  >
                    <span
                      className="h-4 w-4 rounded-full border border-charcoal/10"
                      style={{ backgroundColor: theme.accent }}
                      aria-hidden="true"
                    />
                    {theme.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-ivory/70 p-4">
          <h3 className="text-sm font-semibold text-charcoal">Interface</h3>
          {!isSimpleMode ? (
            <p className="mt-1 text-sm text-taupe">Switch between a cleaner workflow and the fuller guided experience.</p>
          ) : null}
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {(["simple", "explanation"] as UiMode[]).map((mode) => {
              const selected = uiMode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  className={`min-h-11 rounded-xl border px-3 text-left text-sm font-semibold transition ${
                    selected ? "border-orangeBorder bg-orangeSoft text-bronze" : "border-linen bg-white text-charcoal hover:border-orangeBorder"
                  }`}
                  onClick={() => onUiModeChange(mode)}
                  aria-pressed={selected}
                >
                  {mode === "simple" ? "Simple Mode" : "Explanation Mode"}
                  {!isSimpleMode ? (
                    <span className="mt-1 block text-xs font-medium text-taupe">
                      {mode === "simple" ? "Minimal descriptions and cleaner cards." : "Helper text, onboarding descriptions, and extra context."}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
          <div className="mt-3 text-xs font-semibold uppercase tracking-[0.12em] text-taupe">
            Current UI mode: <span className="text-bronze">{isSimpleMode ? "Simple" : "Explanation"}</span>
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-ivory/70 p-4">
          <h3 className="text-sm font-semibold text-charcoal">Developer</h3>
          <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
            <input
              type="checkbox"
              checked={debugMode}
              onChange={(event) => onDebugModeChange(event.target.checked)}
              className="mt-1 h-4 w-4 accent-bronze"
            />
            Enable Debug Mode. Shows sanitized trace details and copyable reports for troubleshooting.
          </label>
          {!debugMode && !isSimpleMode ? (
            <p className="mt-3 text-xs leading-5 text-taupe">
              Developer banners, route details, API URLs, build hashes, and backend diagnostics stay hidden until Debug Mode is enabled.
            </p>
          ) : null}
          {debugMode ? (
            <div className="mt-4 grid gap-4">
              {!isSimpleMode ? (
                <p className="text-xs leading-5 text-taupe">
                  Debug reports include action names, endpoint paths, status codes, timestamps, item identifiers, and sanitized errors. API keys,
                  credentials, auth tokens, and full request payloads are not included.
                </p>
              ) : null}
              <details className="rounded-xl border border-linen bg-white p-3">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.12em] text-charcoal/60">
                  Debug details
                </summary>
                <dl className="mt-3 grid gap-2 text-xs text-taupe">
                  <SettingsDetail label="Internal" value="Enabled" />
                  <SettingsDetail label="Repo" value={buildInfo.repo} />
                  <SettingsDetail label="Branch" value={buildInfo.branch} />
                  <SettingsDetail label="Live route" value={buildInfo.homepageRoute} />
                  <SettingsDetail label="Build hash" value={buildInfo.commit} />
                  <SettingsDetail label="Build timestamp" value={buildInfo.builtAt} />
                  <SettingsDetail label="Backend status" value={`${statusLabel}: ${apiStatusText}`} />
                  <SettingsDetail label="Environment" value={buildInfo.environment} />
                  <SettingsDetail label="Version" value={`v${buildInfo.version}`} />
                  <SettingsDetail label="Vercel project" value={buildInfo.project} />
                  <SettingsDetail label="Root directory" value={buildInfo.rootDirectory} />
                  <SettingsDetail label="Settings renderer" value={buildInfo.settingsRoute} />
                  <SettingsDetail label="Workflow renderer" value={buildInfo.workflowComponent} />
                  <SettingsDetail label="NEXT_PUBLIC_API_BASE_URL" value={rawApiBase} />
                  <SettingsDetail label="Resolved API base" value={apiBase} />
                  <SettingsDetail label="Last endpoint" value={lastEndpoint || "No API request yet"} />
                  {buildInfo.deploymentUrl ? (
                    <SettingsDetail label="Deployment URL" value={buildInfo.deploymentUrl} />
                  ) : null}
                </dl>
                <div className="mt-4 grid gap-3">
                  {frontendRouteWiring.map((route) => (
                    <div key={`${route.action}-${route.endpoint}`} className="rounded-xl border border-linen bg-ivory/60 p-3">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <div className="text-sm font-semibold text-charcoal">{route.action}</div>
                        <code className="rounded-lg bg-white px-2 py-1 text-xs text-bronze">{route.endpoint}</code>
                      </div>
                      <dl className="mt-2 grid gap-1 text-xs text-taupe">
                        <SettingsDetail label="Request body" value={route.body} />
                        <SettingsDetail label="Response" value={route.response} />
                      </dl>
                    </div>
                  ))}
                </div>
              </details>
            </div>
          ) : null}
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-white p-4">
          <h3 className="text-sm font-semibold text-charcoal">About</h3>
          <dl className="mt-3 grid gap-2 text-xs text-taupe">
            <SettingsDetail label="Version" value={`v${buildInfo.version}`} />
            <SettingsDetail label="Build" value={`${buildInfo.commit} · ${buildInfo.builtAt}`} />
            <SettingsDetail label="Environment" value={buildInfo.environment} />
          </dl>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-charcoal">Integrations</h3>
              {!isSimpleMode ? (
                <p className="mt-1 text-sm text-taupe">Backend-only provider status for optional enrichment and image workflows.</p>
              ) : null}
            </div>
            <StatusBadge value={`OpenAI: ${openAiStatusLabel}`} />
          </div>
          <div className="mt-4 rounded-xl border border-linen bg-ivory/60 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h4 className="text-sm font-semibold text-charcoal">OpenAI</h4>
                <p className="mt-1 text-xs text-taupe">
                  Used only by optional Further Enrichment after standard enrichment leaves missing fields.
                </p>
              </div>
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                  openAiConfigured ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-linen bg-white text-taupe"
                }`}
              >
                {openAiStatusLabel}
              </span>
            </div>
            <dl className="mt-3 grid gap-2 text-xs text-taupe">
              <SettingsDetail label="Model used" value={openAiModel} />
              <SettingsDetail label="Further Enrichment" value={furtherEnrichmentEnabled ? "Enabled" : "Disabled"} />
              <SettingsDetail label="Cost cap" value={formatUsd(furtherEnrichmentBudgetUsd)} />
            </dl>
          </div>
        </div>

        <div className="mt-5 rounded-xl border border-linen bg-ivory/70 p-4">
          <h3 className="text-sm font-semibold text-charcoal">Parsing &amp; Enrichment</h3>
          <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
            <input
              type="checkbox"
              checked={useAiPdf}
              onChange={(event) => onUseAiPdfChange(event.target.checked)}
              className="mt-1 h-4 w-4 accent-bronze"
            />
            Use AI for PDFs when deterministic parsing is incomplete.
          </label>
          <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
            <input
              type="checkbox"
              checked={useWebEnrichment}
              onChange={(event) => onUseWebEnrichmentChange(event.target.checked)}
              className="mt-1 h-4 w-4 accent-bronze"
            />
            Use web enrichment for missing product data.
          </label>
          <Field label="Enrichment Mode">
            <select
              className="input-surface mt-3 h-10 w-full rounded-xl px-3 text-sm text-charcoal"
              value={enrichmentMode}
              onChange={(event) => onEnrichmentModeChange(event.target.value as EnrichmentMode)}
            >
              <option value="fast">Fast: lowest external lookup budget</option>
              <option value="standard">Standard: default backend balance</option>
              <option value="deep">Deep: broader lookup, use intentionally</option>
            </select>
          </Field>
          <Field label="Max enrichment cost per document">
            <input
              type="number"
              min="0"
              max="5"
              step="0.01"
              className="input-surface mt-3 h-10 w-full rounded-xl px-3 text-sm text-charcoal"
              value={enrichmentBudgetUsd}
              onChange={(event) => onEnrichmentBudgetUsdChange(clampBudgetUsd(Number.parseFloat(event.target.value)))}
            />
          </Field>
          <div className="mt-4 rounded-xl border border-linen bg-white/75 p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h4 className="text-sm font-semibold text-charcoal">Further Enrichment</h4>
                <p className="mt-1 text-sm leading-5 text-taupe">
                  Use AI reasoning to research missing product details after standard enrichment fails. Higher accuracy, slower, and may cost more.
                </p>
              </div>
              <StatusBadge value={furtherEnrichmentEnabled ? "Enabled" : "Off"} />
            </div>
            <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
              <input
                type="checkbox"
                checked={furtherEnrichmentEnabled}
                onChange={(event) => onFurtherEnrichmentEnabledChange(event.target.checked)}
                className="mt-1 h-4 w-4 accent-bronze"
              />
              Enable Further Enrichment for rows with missing dimensions, missing images, or low confidence.
            </label>
            <Field label="Max cost cap for further enrichment">
              <input
                type="number"
                min="0"
                max="5"
                step="0.01"
                className="input-surface mt-3 h-10 w-full rounded-xl px-3 text-sm text-charcoal"
                value={furtherEnrichmentBudgetUsd}
                onChange={(event) => onFurtherEnrichmentBudgetUsdChange(clampBudgetUsd(Number.parseFloat(event.target.value)))}
              />
            </Field>
            <dl className="mt-3 grid gap-2 text-xs text-taupe">
              <SettingsDetail label="Rows currently eligible" value={String(furtherEnrichmentCandidateCount)} />
              <SettingsDetail label="Estimated next run" value={formatUsd(furtherEnrichmentEstimatedCost, 4)} />
              <SettingsDetail label="Writeback rule" value="Only blank/low-confidence fields; high-confidence values are preserved." />
            </dl>
          </div>
          <label className="mt-3 flex items-start gap-3 text-sm text-taupe">
            <input
              type="checkbox"
              checked={forceRefreshEnrichment}
              onChange={(event) => onForceRefreshEnrichmentChange(event.target.checked)}
              className="mt-1 h-4 w-4 accent-bronze"
            />
            Force refresh enrichment cache on the next enrichment/image recovery run.
          </label>
          <dl className="mt-4 grid gap-2 text-xs text-taupe">
            <SettingsDetail label="Current mode" value={useWebEnrichment ? enrichmentMode : "Local validation only"} />
            <SettingsDetail label="Budget cap" value={formatUsd(enrichmentBudgetUsd)} />
            <SettingsDetail label="Last reported cost" value={estimatedCost} />
            <SettingsDetail label="Budget display" value="Backend stops paid searches before this cap." />
          </dl>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-charcoal">Product Knowledge Base</h3>
              {!isSimpleMode ? (
                <p className="mt-1 text-sm text-taupe">
                  Exact brand + model links are checked before Brave/API search, so repeated products can reuse dimensions, images, and source URLs.
                </p>
              ) : null}
            </div>
            <StatusBadge value={sourceMemoryBackend === "supabase" ? "Persistent DB" : "Runtime fallback"} />
          </div>
          <p className="mt-3 text-xs font-semibold text-bronze">{sourceMemoryStatus}</p>
          <p className="mt-1 text-xs text-taupe">
            {sourceMemoryBackend === "supabase"
              ? `Persistent tables: ${(sourceMemoryAudit.tables || ["stored_product_sources", "preferred_source_domains"]).join(", ")}`
              : sourceMemoryAudit.runtime_cache_persistence_note || "Runtime fallback is temporary unless Supabase is configured."}
          </p>
          <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_180px]">
            <input
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
              value={sourceSearch}
              onChange={(event) => onSourceSearchChange(event.target.value)}
              placeholder="Search brand, model, domain, notes"
            />
            <select
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
              value={sourceTypeFilter}
              onChange={(event) => onSourceTypeFilterChange(event.target.value as SourceTypeFilter)}
            >
              {sourceTypeOptions.map((option) => (
                <option key={option.value || "all-sources"} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="mt-4 rounded-xl border border-linen bg-ivory/60 p-3">
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.brand}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, brand: event.target.value }))}
                placeholder="Brand"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.model_sku}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, model_sku: event.target.value }))}
                placeholder="Model / SKU"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
                value={sourceForm.product_name}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, product_name: event.target.value }))}
                placeholder="Product name / keyword"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
                value={sourceForm.product_page_url}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, product_page_url: event.target.value }))}
                placeholder="Product page URL"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
                value={sourceForm.manufacturer_url}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, manufacturer_url: event.target.value }))}
                placeholder="Manufacturer URL"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.spec_sheet_url}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, spec_sheet_url: event.target.value }))}
                placeholder="Spec sheet URL"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.image_url}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, image_url: event.target.value }))}
                placeholder="Image URL"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
                value={sourceForm.dimension_source_url}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, dimension_source_url: event.target.value }))}
                placeholder="Dimension source URL"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
                value={sourceForm.dimensions}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, dimensions: event.target.value }))}
                placeholder="Dimensions"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.width_in}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, width_in: event.target.value }))}
                placeholder="Width in"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.height_in}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, height_in: event.target.value }))}
                placeholder="Height in"
              />
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.depth_in}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, depth_in: event.target.value }))}
                placeholder="Depth in"
              />
              <select
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
                value={sourceForm.source_type}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, source_type: event.target.value as SourceTypeFilter }))}
              >
                {sourceTypeOptions.filter((option) => option.value).map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
              <input
                className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
                value={sourceForm.notes}
                onChange={(event) => onSourceFormChange((current) => ({ ...current, notes: event.target.value }))}
                placeholder="Notes"
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className="btn-primary inline-flex h-9 items-center rounded-xl px-4 text-xs font-semibold" onClick={onSaveSource}>
                {editingSourceId ? "Update source" : "Add source"}
              </button>
              {editingSourceId ? (
                <button type="button" className="btn-secondary inline-flex h-9 items-center rounded-xl px-4 text-xs font-semibold" onClick={onCancelSourceEdit}>
                  Cancel edit
                </button>
              ) : null}
            </div>
          </div>
          <div className="mt-4 grid gap-2">
            {storedSources.length ? storedSources.slice(0, 12).map((source) => (
              <div key={String(source.id || `${source.brand}-${source.model_sku}-${source.product_page_url}`)} className="rounded-xl border border-linen bg-ivory/50 p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold text-charcoal">{source.display_brand || source.brand || "Unknown brand"} {source.display_model_sku || source.model_sku || ""}</div>
                    <div className="mt-1 truncate text-xs text-taupe">{source.product_page_url || source.manufacturer_url || source.dimension_source_url || source.spec_sheet_url || "No URL saved"}</div>
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-taupe">
                      <span>{source.source_type || "other"}</span>
                      <span>{source.source_domain || "domain pending"}</span>
                      <span>confidence {Number(source.confidence_score || 0)}</span>
                      <span>success {Number(source.success_count || 0)}</span>
                      <span>fail {Number(source.failure_count || 0)}</span>
                      <span>verified {source.last_verified_at ? new Date(String(source.last_verified_at)).toLocaleDateString() : "never"}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" className="btn-secondary rounded-xl px-3 py-2 text-xs font-semibold" onClick={() => onEditSource(source)}>Edit</button>
                    <button type="button" className="btn-secondary rounded-xl px-3 py-2 text-xs font-semibold" onClick={() => onReverifySource(source)}>Re-verify</button>
                    <button type="button" className="btn-secondary rounded-xl px-3 py-2 text-xs font-semibold" onClick={() => onDeleteSource(source)}>Delete</button>
                  </div>
                </div>
                {source.dimensions_text || source.dimensions || source.image_url ? (
                  <div className="mt-2 text-xs text-taupe">
                    {source.dimensions_text || source.dimensions ? <span>Dimensions: {String(source.dimensions_text || source.dimensions)} </span> : null}
                    {source.image_url ? <span>Image saved </span> : null}
                    {source.dimension_confidence ? <span>Dimension confidence: {String(source.dimension_confidence)} </span> : null}
                    {source.image_confidence ? <span>Image confidence: {String(source.image_confidence)}</span> : null}
                  </div>
                ) : null}
              </div>
            )) : (
              <p className="rounded-xl border border-linen bg-ivory/50 p-3 text-sm text-taupe">No stored product sources yet.</p>
            )}
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-ivory/70 p-4">
          <h3 className="text-sm font-semibold text-charcoal">Preferred Source Domains</h3>
          {!isSimpleMode ? (
            <p className="mt-1 text-sm text-taupe">These domains are prioritized before broad search and down-ranked as failures accumulate.</p>
          ) : null}
          <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_180px]">
            <input
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
              value={domainSearch}
              onChange={(event) => onDomainSearchChange(event.target.value)}
              placeholder="Search domain"
            />
            <select
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
              value={domainTypeFilter}
              onChange={(event) => onDomainTypeFilterChange(event.target.value as SourceTypeFilter)}
            >
              {sourceTypeOptions.map((option) => (
                <option key={option.value || "all-domains"} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_170px]">
            <input
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
              value={domainForm.domain}
              onChange={(event) => onDomainFormChange((current) => ({ ...current, domain: event.target.value }))}
              placeholder="subzero-wolf.com"
            />
            <select
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal"
              value={domainForm.source_type}
              onChange={(event) => onDomainFormChange((current) => ({ ...current, source_type: event.target.value as SourceTypeFilter }))}
            >
              {sourceTypeOptions.filter((option) => option.value).map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <input
              className="input-surface h-10 rounded-xl px-3 text-sm text-charcoal sm:col-span-2"
              value={domainForm.notes}
              onChange={(event) => onDomainFormChange((current) => ({ ...current, notes: event.target.value }))}
              placeholder="Notes"
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn-primary inline-flex h-9 items-center rounded-xl px-4 text-xs font-semibold" onClick={onSaveDomain}>
              {editingDomainId ? "Update domain" : "Add domain"}
            </button>
            {editingDomainId ? (
              <button type="button" className="btn-secondary inline-flex h-9 items-center rounded-xl px-4 text-xs font-semibold" onClick={onCancelDomainEdit}>
                Cancel edit
              </button>
            ) : null}
          </div>
          <div className="mt-4 grid gap-2">
            {preferredDomains.length ? preferredDomains.slice(0, 16).map((domain) => (
              <div key={String(domain.id || domain.domain)} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-linen bg-white p-3">
                <div>
                  <div className="font-semibold text-charcoal">{domain.domain || "Unknown domain"}</div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-taupe">
                    <span>{domain.source_type || "other"}</span>
                    <span>success {Number(domain.success_count || 0)}</span>
                    <span>fail {Number(domain.failure_count || 0)}</span>
                    {domain.downranked ? <span>downranked</span> : null}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary rounded-xl px-3 py-2 text-xs font-semibold" onClick={() => onEditDomain(domain)}>Edit</button>
                  <button type="button" className="btn-secondary rounded-xl px-3 py-2 text-xs font-semibold" onClick={() => onDeleteDomain(domain)}>Delete</button>
                </div>
              </div>
            )) : (
              <p className="rounded-xl border border-linen bg-white p-3 text-sm text-taupe">No preferred domains saved yet.</p>
            )}
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-white p-4">
          <h3 className="text-sm font-semibold text-charcoal">Cloudinary / Image Upload</h3>
          <dl className="mt-3 grid gap-2 text-xs text-taupe">
            {debugMode ? (
              <SettingsDetail label="Image upload route" value={apiBase !== "not configured" ? `${apiBase}/api/upload-image` : "Backend not configured"} />
            ) : null}
            <SettingsDetail label="Selected photo uploads" value={`${bulkImagesCount} queued in current upload step`} />
            <SettingsDetail label="Uploaded / failed" value={`${photoBulkSummary.success} uploaded / ${photoBulkSummary.failed} failed`} />
            <SettingsDetail label="Programa image URLs" value={`${imageReadyCount}/${imageTotalCount || 0} rows have image URLs`} />
            {debugMode ? (
              <SettingsDetail label="Product link metadata" value="No dedicated metadata route; links become rows in /intake/generate and are filled during enrichment." />
            ) : null}
          </dl>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-ivory/70 p-4">
          <h3 className="text-sm font-semibold text-charcoal">Export Preferences</h3>
          {!isSimpleMode ? (
            <p className="mt-1 text-sm text-taupe">Excel is the primary Programa handoff. CSV, ZIP, and debug exports stay available after enrichment.</p>
          ) : null}
          <dl className="mt-4 grid gap-2 text-xs text-taupe">
            <SettingsDetail label="Primary export" value="Download Excel for Programa (.xlsx)" />
            <SettingsDetail label="Export-ready rows" value={String(exportReadyCount)} />
            <SettingsDetail label="Direct Programa send" value={programaSendEnabled ? "Configured" : "Disabled until integration is configured"} />
          </dl>
          <Field label="Programa Schedule URL">
            <input
              className="input-surface mt-3 h-10 w-full rounded-xl px-3 text-sm text-charcoal"
              value={scheduleUrl}
              onChange={(event) => onScheduleUrlChange(event.target.value)}
              placeholder="https://app.programa.design/schedules2/schedules/..."
            />
          </Field>
        </div>

      </div>
    </div>
  );
}

function SettingsStatusPill({
  status,
  label,
}: {
  status: "checking" | "online" | "offline" | "misconfigured";
  label: string;
}) {
  const tone =
    status === "online"
      ? "border-sage/20 bg-sage/10 text-sage"
      : status === "checking"
        ? "border-orangeBorder bg-orangeSoft text-bronze"
        : "border-clay/20 bg-clay/10 text-clay";

  return <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${tone}`}>{label}</span>;
}

function SettingsDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[170px_1fr]">
      <dt className="font-semibold uppercase tracking-[0.08em] text-charcoal/50">{label}</dt>
      <dd className="break-all font-mono text-charcoal">{value}</dd>
    </div>
  );
}

function Panel({
  step,
  title,
  subtitle,
  simple = false,
  children,
}: {
  step?: string;
  title: string;
  subtitle: string;
  simple?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-linen bg-white/70 p-5 shadow-panel sm:p-6">
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
          {!simple ? <p className="mt-2 text-sm leading-6 text-charcoal/60">{subtitle}</p> : null}
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
    ["Dimensions", rowText(state.row, "Dimensions")],
    ["Finish", rowText(state.row, "Finish / Color")],
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

function StageProgressTracker({
  stages,
  activeIndex,
  progress,
  onStageClick,
}: {
  stages: {
    label: string;
    title: string;
    detail: string;
    complete: boolean;
    unlocked: boolean;
  }[];
  activeIndex: number;
  progress: number;
  onStageClick: (index: number) => void;
}) {
  return (
    <section className="rounded-[28px] border border-linen bg-white/72 p-4 shadow-panel">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-taupe">Workflow progress</div>
          <div className="mt-1 text-2xl font-semibold text-charcoal">{progress}%</div>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-ivory sm:w-56">
          <div className="h-full rounded-full bg-[var(--accent)] transition-all duration-500" style={{ width: `${progress}%` }} />
        </div>
      </div>
      <div className="grid gap-2 lg:grid-cols-4">
        {stages.map((stage, index) => {
          const isActive = index === activeIndex;
          const stateClass = stage.complete
            ? "border-sage/30 bg-sage/10 text-sage"
            : isActive
              ? "border-orangeBorder bg-orangeSoft text-bronze shadow-sm"
              : stage.unlocked
                ? "border-linen bg-ivory/70 text-charcoal"
                : "border-transparent bg-ivory/50 text-charcoal/45";
          const dotClass = stage.complete
            ? "bg-sage text-white"
            : isActive
              ? "bg-white text-bronze"
              : "bg-white/70 text-charcoal/50";
          return (
            <button
              key={stage.label}
              type="button"
              disabled={!stage.unlocked}
              onClick={() => onStageClick(index)}
              className={`min-h-[104px] rounded-2xl border p-3 text-left transition ${stateClass} disabled:cursor-not-allowed`}
            >
              <div className="flex items-start gap-3">
                <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-bold ${dotClass}`}>
                  {stage.complete ? <CheckCircle2 className="h-4 w-4" /> : index + 1}
                </span>
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.1em]">{stage.label}</div>
                  <div className="mt-1 text-sm font-semibold text-charcoal">{stage.title}</div>
                  <div className="mt-1 text-xs leading-5 text-current/75">{stage.detail}</div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </section>
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
