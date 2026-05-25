import type {
  IntakeResponse,
  IntakeRow,
  ProgramaExportValidation,
  SchemaResponse,
  VendorCallRefreshResponse,
  VendorCallResponse,
  VendorCallStartResponse,
  VendorCallStatus,
} from "./types";

const RAW_API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "";
const BACKEND_UNAVAILABLE = "Backend is offline or not configured.";

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

function apiUrl(path: string) {
  if (!API_BASE) throw new Error(BACKEND_UNAVAILABLE);
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
}): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/enrich"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rows: input.rows,
        use_web_enrichment: input.useWebEnrichment,
      }),
    }),
  );
}

export async function recoverImages(rows: IntakeRow[]): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/recover-images"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
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

async function exportProgramaFile(rows: IntakeRow[], path: string, fallbackMessage: string): Promise<Blob> {
  const response = await apiFetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!response.ok) throw new Error(fallbackMessage);
  return response.blob();
}

export async function exportProgramaCsv(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/csv", "Could not export Programa CSV.");
}

export async function exportProgramaXlsx(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/xlsx", "Could not export Programa XLSX.");
}

export async function exportProgramaXlsxWithImages(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(
    rows,
    "/export/programa/xlsx-with-images",
    "Could not export Programa Excel with Images.",
  );
}

export async function exportProgramaZip(rows: IntakeRow[], includeLowConfidenceImages = false): Promise<Blob> {
  const response = await apiFetch(apiUrl("/export/programa/zip"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rows,
      include_low_confidence_images: includeLowConfidenceImages,
    }),
  });
  if (!response.ok) throw new Error("Could not export Programa ZIP.");
  return response.blob();
}

export async function exportProgramaDebugCsv(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/debug-csv", "Could not export Debug CSV.");
}
