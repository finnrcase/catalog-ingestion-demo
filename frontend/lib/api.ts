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

export const RAW_API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "";
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

export const API_BASE = resolveApiBase(RAW_API_BASE);

if (typeof window !== "undefined") {
  console.info(`[API BASE URL] ${API_BASE || "not configured"}`);
}

export function apiUrl(path: string) {
  if (!API_BASE) throw new Error(BACKEND_UNAVAILABLE);
  return `${API_BASE}${path}`;
}

type ApiDebugDetails = {
  apiBase: string;
  endpoint: string;
  status?: number;
  responseText?: string;
  kind: "config" | "network-or-cors" | "http";
};

export class ApiRequestError extends Error {
  details: ApiDebugDetails;

  constructor(message: string, details: ApiDebugDetails) {
    super(message);
    this.name = "ApiRequestError";
    this.details = details;
  }
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
  const endpoint =
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

  try {
    return await fetch(input, init);
  } catch (error) {
    if (error instanceof Error && error.message === BACKEND_UNAVAILABLE) {
      throw new ApiRequestError(BACKEND_UNAVAILABLE, {
        apiBase: API_BASE || RAW_API_BASE || "not configured",
        endpoint,
        kind: "config",
      });
    }

    throw new ApiRequestError(
      error instanceof Error ? error.message : "Network request failed.",
      {
        apiBase: API_BASE || RAW_API_BASE || "not configured",
        endpoint,
        kind: "network-or-cors",
      },
    );
  }
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    await throwResponseError(response, `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function throwResponseError(response: Response, fallback: string): Promise<never> {
  const responseText = await response.text();
  let message = fallback;

  try {
    const body = JSON.parse(responseText);
    if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    if (responseText) message = fallback;
  }

  throw new ApiRequestError(message, {
    apiBase: API_BASE || RAW_API_BASE || "not configured",
    endpoint: response.url,
    status: response.status,
    responseText,
    kind: "http",
  });
}

export function formatApiError(error: unknown) {
  if (!(error instanceof ApiRequestError)) {
    return error instanceof Error ? error.message : "Request failed.";
  }

  const { apiBase, endpoint, status, responseText, kind } = error.details;
  const cause =
    kind === "network-or-cors"
      ? "Network/CORS/preflight or browser-blocked request"
      : kind === "config"
        ? "Missing or invalid frontend API base URL"
        : status === 404
          ? "Backend route not found"
          : status && status >= 500
            ? "Backend server error"
            : "Backend returned a non-OK response";

  return [
    error.message,
    `API base URL: ${apiBase}`,
    `Endpoint called: ${endpoint}`,
    `Status: ${status ?? "unavailable"}`,
    `Failure type: ${cause}`,
    `Response text: ${responseText || "unavailable"}`,
  ].join("\n");
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
}): Promise<IntakeResponse> {
  const form = new FormData();
  form.append("project", input.project);
  form.append("room", input.room);
  form.append("urls", input.urls);
  form.append("use_ai_pdf", String(input.useAiPdf));
  input.files.forEach((file) => form.append("files", file));

  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/generate"), { method: "POST", body: form }),
  );
}

export const generateIntake = generateIntakeTable;

export async function uploadImage(file: File): Promise<{ secure_url: string }> {
  const form = new FormData();
  form.append("file", file);
  return parseJson<{ secure_url: string }>(
    await apiFetch(apiUrl("/api/upload-image"), { method: "POST", body: form }),
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
  enrichmentMode?: string;
  forceRefresh?: boolean;
}): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/enrich"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rows: input.rows,
        use_web_enrichment: input.useWebEnrichment,
        enrichment_mode: input.enrichmentMode,
        force_refresh: input.forceRefresh ?? false,
      }),
    }),
  );
}

export async function recoverMissingImages(input: {
  rows: IntakeRow[];
  enrichmentMode?: string;
  forceRefresh?: boolean;
}): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(apiUrl("/intake/recover-images"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rows: input.rows,
        enrichment_mode: input.enrichmentMode,
        force_refresh: input.forceRefresh ?? false,
      }),
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
  if (!response.ok) await throwResponseError(response, "Could not export CSV.");
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
  if (!response.ok) await throwResponseError(response, fallbackMessage);
  return response.blob();
}

export async function exportProgramaCsv(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/csv", "Could not export Programa CSV.");
}

export async function exportProgramaXlsx(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/xlsx", "Could not export Programa XLSX.");
}

export async function exportProgramaDebugCsv(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/debug-csv", "Could not export Debug CSV.");
}

export async function exportProgramaZip(rows: IntakeRow[]): Promise<Blob> {
  return exportProgramaFile(rows, "/export/programa/zip", "Could not export Programa ZIP.");
}
