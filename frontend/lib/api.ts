import type {
  IntakeResponse,
  IntakeRow,
  PdfParseJob,
  ProgramaExportValidation,
  PreferredWebsiteEntry,
  SchemaResponse,
  VendorCallRefreshResponse,
  VendorCallResponse,
  VendorCallStartResponse,
  VendorCallStatus,
} from "./types";

const RAW_API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "";
const BACKEND_UNAVAILABLE = "Backend is offline or not configured.";
const LOCAL_BACKEND_CONFIGURED =
  "Backend API URL is set to localhost, which only works on your machine. Set NEXT_PUBLIC_API_BASE_URL to the deployed backend URL.";

function isLocalHost(hostname: string) {
  return ["localhost", "127.0.0.1", "0.0.0.0", "::1"].includes(hostname.toLowerCase());
}

function resolveApiBase(rawUrl: string) {
  if (!rawUrl || rawUrl === "undefined" || rawUrl === "null") return "";
  try {
    const parsed = new URL(rawUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    return parsed.href.replace(/\/$/, "");
  } catch {
    return "";
  }
}

const API_BASE = resolveApiBase(RAW_API_BASE);

if (typeof window !== "undefined") {
  console.info(`[API BASE URL] ${API_BASE || "not configured"}`);
}

function apiBaseIsUsableInThisBrowser() {
  if (!API_BASE || typeof window === "undefined") return true;
  try {
    const apiHost = new URL(API_BASE).hostname;
    const pageHost = window.location.hostname;
    return !isLocalHost(apiHost) || isLocalHost(pageHost);
  } catch {
    return false;
  }
}

function apiUrl(path: string) {
  if (!API_BASE) throw new Error(BACKEND_UNAVAILABLE);
  if (!apiBaseIsUsableInThisBrowser()) throw new Error(LOCAL_BACKEND_CONFIGURED);
  return `${API_BASE}${path}`;
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
  try {
    return await fetch(input, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof Error && error.message === BACKEND_UNAVAILABLE) throw error;
    throw new Error(BACKEND_UNAVAILABLE);
  }
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const fallback = `Request failed with status ${response.status}`;
    try {
      const body = await response.json();
      throw new Error(body.detail || fallback);
    } catch (error) {
      if (error instanceof Error && error.message !== fallback) throw error;
      throw new Error(fallback);
    }
  }
  return response.json() as Promise<T>;
}

export async function fetchSchema(): Promise<SchemaResponse> {
  return parseJson<SchemaResponse>(await apiFetch(apiUrl("/schema"), { cache: "no-store" }));
}

export async function fetchHealth(): Promise<{ status: string }> {
  return parseJson<{ status: string }>(await apiFetch(apiUrl("/health"), { cache: "no-store" }));
}

export async function generateIntakeTable(input: {
  project: string;
  room: string;
  urls: string;
  useAiPdf: boolean;
  files: File[];
}, options?: { signal?: AbortSignal }): Promise<IntakeResponse> {
  const form = new FormData();
  form.append("project", input.project);
  form.append("room", input.room);
  form.append("urls", input.urls);
  form.append("use_ai_pdf", String(input.useAiPdf));
  input.files.forEach((file) => form.append("files", file));

  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/generate"), { method: "POST", body: form, signal: options?.signal }),
  );
}

export const generateIntake = generateIntakeTable;

export async function uploadPdfForParsing(input: {
  file: File;
  project: string;
  room: string;
  sessionId?: string;
}, options?: { signal?: AbortSignal }): Promise<{
  session_id: string;
  pdf_id: string;
  parse_job_id: string;
  status: string;
  stage: string;
  rows: IntakeRow[];
}> {
  const form = new FormData();
  form.append("project", input.project);
  form.append("room", input.room);
  form.append("file", input.file);
  const headers: HeadersInit = {};
  if (input.sessionId) headers["X-Session-Id"] = input.sessionId;
  return parseJson<{
    session_id: string;
    pdf_id: string;
    parse_job_id: string;
    status: string;
    stage: string;
    rows: IntakeRow[];
  }>(
    await apiFetch(apiUrl("/intake/upload-pdf"), {
      method: "POST",
      body: form,
      headers,
      signal: options?.signal,
    }),
  );
}

export async function fetchPdfParseJob(jobId: string): Promise<PdfParseJob> {
  return parseJson<PdfParseJob>(await apiFetch(apiUrl(`/intake/pdf-jobs/${jobId}`), { cache: "no-store" }));
}

export async function retryPdfParseJob(jobId: string): Promise<PdfParseJob> {
  return parseJson<PdfParseJob>(await apiFetch(apiUrl(`/intake/pdf-jobs/${jobId}/retry`), { method: "POST" }));
}

export async function cancelPdfParseJob(jobId: string): Promise<PdfParseJob> {
  return parseJson<PdfParseJob>(await apiFetch(apiUrl(`/intake/pdf-jobs/${jobId}/cancel`), { method: "POST" }));
}

export async function fetchPdfParseLogs(jobId: string): Promise<PdfParseJob> {
  return parseJson<PdfParseJob>(await apiFetch(apiUrl(`/intake/pdf-jobs/${jobId}/logs`), { cache: "no-store" }));
}

export async function uploadImage(file: File, options?: { signal?: AbortSignal }): Promise<{ secure_url: string }> {
  const form = new FormData();
  form.append("file", file);
  return parseJson<{ secure_url: string }>(
    await apiFetch(apiUrl("/api/upload-image"), { method: "POST", body: form, signal: options?.signal }),
  );
}

export async function validateRows(rows: IntakeRow[]): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/validate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    }),
  );
}

export async function enrichRows(input: {
  rows: IntakeRow[];
  useWebEnrichment: boolean;
  sessionId?: string;
  enrichmentMode?: "fast" | "standard" | "deep" | "manual_retry";
  targetedRetryMode?: "off" | "conservative" | "balanced" | "aggressive";
  maxExtraRetriesPerItem?: number;
  maxExtraCostPerRow?: number;
  maxExtraCostPerRun?: number;
}): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/enrich"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rows: input.rows,
        use_web_enrichment: input.useWebEnrichment,
        session_id: input.sessionId,
        enrichment_mode: input.enrichmentMode || "fast",
        targeted_retry_mode: input.targetedRetryMode || "conservative",
        max_extra_retries_per_item: input.maxExtraRetriesPerItem,
        max_extra_cost_per_row: input.maxExtraCostPerRow,
        max_extra_cost_per_run: input.maxExtraCostPerRun,
      }),
    }),
  );
}

export async function recoverImages(rows: IntakeRow[], sessionId?: string): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/recover-images"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows, session_id: sessionId }),
    }),
  );
}

export async function saveManufacturerOverride(input: {
  brand: string;
  website: string;
}): Promise<{ status: string; override: { brand: string; domain: string; source: string; last_verified: string } }> {
  return parseJson<{ status: string; override: { brand: string; domain: string; source: string; last_verified: string } }>(
    await apiFetch(apiUrl("/manufacturer-override"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function fetchPreferredWebsites(): Promise<{ entries: PreferredWebsiteEntry[] }> {
  return parseJson<{ entries: PreferredWebsiteEntry[] }>(
    await apiFetch(apiUrl("/settings/preferred-websites"), { cache: "no-store" }),
  );
}

export async function createPreferredWebsite(input: {
  keyword: string;
  url: string;
  notes?: string;
}): Promise<{ status: string; entry: PreferredWebsiteEntry; entries: PreferredWebsiteEntry[] }> {
  return parseJson<{ status: string; entry: PreferredWebsiteEntry; entries: PreferredWebsiteEntry[] }>(
    await apiFetch(apiUrl("/settings/preferred-websites"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function updatePreferredWebsite(
  entryId: string,
  input: { keyword: string; url: string; notes?: string },
): Promise<{ status: string; entry: PreferredWebsiteEntry; entries: PreferredWebsiteEntry[] }> {
  return parseJson<{ status: string; entry: PreferredWebsiteEntry; entries: PreferredWebsiteEntry[] }>(
    await apiFetch(apiUrl(`/settings/preferred-websites/${encodeURIComponent(entryId)}`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function deletePreferredWebsite(entryId: string): Promise<{ status: string; entries: PreferredWebsiteEntry[] }> {
  return parseJson<{ status: string; entries: PreferredWebsiteEntry[] }>(
    await apiFetch(apiUrl(`/settings/preferred-websites/${encodeURIComponent(entryId)}`), { method: "DELETE" }),
  );
}

export async function sendToPrograma(input: {
  projectName: string;
  scheduleUrl: string;
  rows: IntakeRow[];
  allowBlankFields: boolean;
  uploadProductImages?: boolean;
}) {
  return parseJson<{
    status: string;
    message?: string;
    allow_blank_fields?: boolean;
    entries?: Record<string, unknown>[];
    log_path?: string;
    blocked?: IntakeRow[];
  }>(
    await apiFetch(apiUrl("/programa/send"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_name: input.projectName,
        schedule_url: input.scheduleUrl,
        rows: input.rows,
        auto_done: false,
        allow_blank_fields: input.allowBlankFields,
        upload_product_images: input.uploadProductImages ?? true,
      }),
    }),
  );
}

export const sendRows = sendToPrograma;

export async function openProgramaLogin() {
  return parseJson<{ status: string; message?: string }>(
    await apiFetch(apiUrl("/programa/login"), { method: "POST" }),
  );
}

export async function generateVendorCallScript(input: {
  row: IntakeRow;
  missingFields: string[];
  phoneNumber: string;
  customGoal: string;
}) {
  return parseJson<VendorCallResponse>(
    await apiFetch(apiUrl("/vendor-call/script"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        row: input.row,
        missing_fields: input.missingFields,
        phone_number: input.phoneNumber,
        custom_goal: input.customGoal,
      }),
    }),
  );
}

export async function fetchVendorCallStatus() {
  return parseJson<VendorCallStatus>(
    await apiFetch(apiUrl("/vendor-call/status"), { cache: "no-store" }),
  );
}

export async function startVendorCall(input: {
  row: IntakeRow;
  missingFields: string[];
  phoneNumber: string;
  customGoal: string;
}) {
  return parseJson<VendorCallStartResponse>(
    await apiFetch(apiUrl("/vendor-call/start"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        row: input.row,
        missing_fields: input.missingFields,
        phone_number: input.phoneNumber,
        custom_goal: input.customGoal,
      }),
    }),
  );
}

export async function refreshVendorCall(input: {
  callId: string;
  missingFields: string[];
}) {
  return parseJson<VendorCallRefreshResponse>(
    await apiFetch(apiUrl("/vendor-call/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        call_id: input.callId,
        missing_fields: input.missingFields,
      }),
    }),
  );
}

export async function exportReviewCsv(rows: IntakeRow[]): Promise<Blob> {
  const response = await apiFetch(apiUrl("/export/csv"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!response.ok) throw new Error("Could not export CSV.");
  return response.blob();
}

export const downloadCsv = exportReviewCsv;

export async function validateProgramaExport(rows: IntakeRow[]): Promise<ProgramaExportValidation> {
  return parseJson<ProgramaExportValidation>(
    await apiFetch(apiUrl("/export/programa/validate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    }),
  );
}

export type ProgramaExportFile = {
  blob: Blob;
  filename: string;
};

function filenameFromContentDisposition(header: string | null, fallback: string) {
  if (!header) return fallback;
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1].replace(/"/g, ""));
    } catch {
      return utf8Match[1].replace(/"/g, "");
    }
  }
  const match = header.match(/filename="?([^";]+)"?/i);
  return match?.[1]?.trim() || fallback;
}

async function blobStartsWithZipSignature(blob: Blob) {
  const bytes = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  return bytes[0] === 0x50 && bytes[1] === 0x4b;
}

async function exportProgramaFile(
  rows: IntakeRow[],
  path: string,
  fallbackMessage: string,
  fallbackFilename: string,
  expectedFormat: "csv" | "xlsx",
): Promise<ProgramaExportFile> {
  const response = await apiFetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!response.ok) throw new Error(fallbackMessage);
  const blob = await response.blob();
  if (expectedFormat === "xlsx" && !(await blobStartsWithZipSignature(blob))) {
    throw new Error("XLSX export returned a non-Excel file. Please use CSV export or retry.");
  }
  return {
    blob,
    filename: filenameFromContentDisposition(response.headers.get("content-disposition"), fallbackFilename),
  };
}

export async function exportProgramaCsv(rows: IntakeRow[]): Promise<ProgramaExportFile> {
  return exportProgramaFile(rows, "/export/programa/csv", "Could not export Programa CSV.", "programa_import.csv", "csv");
}

export async function exportProgramaXlsx(rows: IntakeRow[]): Promise<ProgramaExportFile> {
  return exportProgramaFile(rows, "/export/programa/xlsx", "Could not export Programa XLSX.", "programa_import.xlsx", "xlsx");
}

export async function exportProgramaXlsxWithImages(rows: IntakeRow[]): Promise<ProgramaExportFile> {
  return exportProgramaFile(
    rows,
    "/export/programa/xlsx-with-images",
    "Could not export Programa Excel with Images.",
    "programa_import_with_images.xlsx",
    "xlsx",
  );
}

export async function exportProgramaZip(rows: IntakeRow[], includeLowConfidenceImages = false): Promise<ProgramaExportFile> {
  const response = await apiFetch(apiUrl("/export/programa/zip"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rows,
      include_low_confidence_images: includeLowConfidenceImages,
    }),
  });
  if (!response.ok) throw new Error("Could not export Programa ZIP.");
  return {
    blob: await response.blob(),
    filename: filenameFromContentDisposition(response.headers.get("content-disposition"), "programa_export.zip"),
  };
}

export async function exportProgramaDebugCsv(rows: IntakeRow[]): Promise<Blob> {
  return (await exportProgramaFile(
    rows,
    "/export/programa/debug-csv",
    "Could not export Debug CSV.",
    "programa_import_debug.csv",
    "csv",
  )).blob;
}
