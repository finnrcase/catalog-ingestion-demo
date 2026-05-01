import type {
  IntakeResponse,
  IntakeRow,
  SchemaResponse,
  VendorCallRefreshResponse,
  VendorCallResponse,
  VendorCallStartResponse,
  VendorCallStatus,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const BACKEND_UNAVAILABLE = "Backend unavailable. Check NEXT_PUBLIC_API_BASE_URL.";

async function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
  try {
    return await fetch(input, init);
  } catch {
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
  return parseJson<SchemaResponse>(await apiFetch(`${API_BASE}/schema`, { cache: "no-store" }));
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
    await apiFetch(`${API_BASE}/intake/generate`, { method: "POST", body: form }),
  );
}

export const generateIntake = generateIntakeTable;

export async function validateRows(rows: IntakeRow[]): Promise<IntakeResponse> {
  return parseJson<IntakeResponse>(
    await apiFetch(`${API_BASE}/intake/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
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
    await apiFetch(`${API_BASE}/programa/send`, {
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
    await apiFetch(`${API_BASE}/programa/login`, { method: "POST" }),
  );
}

export async function generateVendorCallScript(input: {
  row: IntakeRow;
  missingFields: string[];
  phoneNumber: string;
  customGoal: string;
}) {
  return parseJson<VendorCallResponse>(
    await apiFetch(`${API_BASE}/vendor-call/script`, {
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
    await apiFetch(`${API_BASE}/vendor-call/status`, { cache: "no-store" }),
  );
}

export async function startVendorCall(input: {
  row: IntakeRow;
  missingFields: string[];
  phoneNumber: string;
  customGoal: string;
}) {
  return parseJson<VendorCallStartResponse>(
    await apiFetch(`${API_BASE}/vendor-call/start`, {
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
    await apiFetch(`${API_BASE}/vendor-call/refresh`, {
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
  const response = await apiFetch(`${API_BASE}/export/csv`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!response.ok) throw new Error("Could not export CSV.");
  return response.blob();
}

export const downloadCsv = exportReviewCsv;
