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
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  exportProgramaCsv,
  exportProgramaDebugCsv,
  exportProgramaXlsx,
  exportProgramaZip,
  formatApiError,
  API_BASE,
  fetchHealth,
  fetchSchema,
  fetchVendorCallStatus,
  enrichRows,
  generateIntakeTable,
  generateVendorCallScript,
  refreshVendorCall,
  sendToPrograma,
  startVendorCall,
  uploadImage,
  validateProgramaExport,
  validateRows,
} from "@/lib/api";
import { hasComplete3dDimensions } from "@/lib/dimensions";
import type { IntakeRow } from "@/lib/types";

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
  deploymentUrl?: string;
};

function rowText(row: IntakeRow, key: string) {
  return String(row[key] ?? "");
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

const fallbackBuildInfo: BuildInfo = {
  commit: "local",
  builtAt: "local",
  version: "0.1.0",
  deploymentUrl: "",
};

function formatBuildTime(value: string) {
  if (!value || value === "local") return "local";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [parsedProductsOpen, setParsedProductsOpen] = useState(false);
  const [enrichedProductsOpen, setEnrichedProductsOpen] = useState(false);
  const [parseStatus, setParseStatus] = useState("Ready for upload.");
  const [enrichmentStatus, setEnrichmentStatus] = useState("Not started.");
  const [apiStatus, setApiStatus] = useState<"checking" | "online" | "offline">("checking");
  const [apiStatusText, setApiStatusText] = useState("Checking backend...");
  const [lastEndpoint, setLastEndpoint] = useState("");
  const [estimatedCost, setEstimatedCost] = useState("Not reported");
  const [enrichmentStats, setEnrichmentStats] = useState({
    filledImages: 0,
    filledDimensions: 0,
    unresolved: 0,
  });
  const [scheduleUrl, setScheduleUrl] = useState("");
  const [programaMessage, setProgramaMessage] = useState("");
  const [productImageUploads, setProductImageUploads] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<"generate" | "validate" | "vendorCall" | "export" | "photoBulk" | "programa" | "">("");
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
    duplicates_removed: [] as { index: number; product_name: string; brand?: string; sku?: string; kept_index?: number; reason?: string }[],
    duplicate_rows_removed: 0,
    suspicious_dimensions_rejected: [] as { index: number; product_name: string; brand?: string; sku?: string; dimensions?: string; reason?: string }[],
    rejected_product_urls: [] as { index: number; product_name: string; brand?: string; sku?: string; url?: string; reason?: string }[],
    pdf_product_urls: [] as { index: number; product_name: string; brand?: string; sku?: string; url?: string; reason?: string }[],
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
      })
      .catch((error) => {
        setApiStatus("offline");
        setApiStatusText(formatApiError(error));
        setMessage(formatApiError(error));
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
  const unresolvedCount = missingInputRows.length;
  const parseInputCount = files.length + bulkImages.length + urls.split(/\r?\n/).filter((url) => url.trim()).length;
  const programaSendEnabled = process.env.NEXT_PUBLIC_PROGRAMA_SEND_ENABLED === "true";

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
        if (fileInputRef.current) fileInputRef.current.value = "";
        return;
      }
      setFiles(nextFiles);
    } catch {
      setFiles([]);
      setUploadError("Upload failed. Please choose the PDF again.");
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
      return;
    }
    setBulkImages(nextFiles);
    setPhotoBulkResults({});
    setPhotoBulkSummary({ success: 0, failed: 0 });
  }

  function clearBulkImages() {
    setBulkImages([]);
    setBulkImageError("");
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
    const nextResults: typeof photoBulkResults = {};
    let success = 0;
    let failed = 0;
    const createdRows: IntakeRow[] = [];
    const startIndex = rows.length;

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
      } catch (error) {
        const row = createPhotoOnlyRow(file, index, "", "Missing Image");
        createdRows.push(row);
        nextResults[key] = {
          status: "failed",
          error: error instanceof Error ? error.message : "Upload failed.",
          rowIndex: startIndex + createdRows.length - 1,
        };
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
    } catch {
      setRows((current) => [...current, ...createdRows]);
      setMessage(`Photo-only bulk import created ${success} row${success === 1 ? "" : "s"} with images; ${failed} failed.`);
    } finally {
      setBusy("");
    }
  }

  async function retryPhotoUpload(file: File, index: number) {
    const key = bulkImageKey(file, index);
    const result = photoBulkResults[key];
    if (!result || result.rowIndex === undefined) return;
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
    } catch (error) {
      setProductImageUploads((current) => ({
        ...current,
        [rowIndex]: error instanceof Error ? error.message : "Upload failed.",
      }));
    }
  }

  async function handleGenerate() {
    setBusy("generate");
    setMessage("");
    setErrors([]);
    setParseStatus("Parsing uploaded files and links...");
    setLastEndpoint(`${API_BASE || "not configured"}/intake/generate`);
    try {
      const response = await generateIntakeTable({ project, room, urls, useAiPdf, files });
      setRows(response.rows);
      setErrors(response.errors);
      setParseStatus(`Parse complete: ${response.rows.length} product${response.rows.length === 1 ? "" : "s"} found.`);
      setParsedProductsOpen(false);
      setEnrichedProductsOpen(false);
      setEstimatedCost(getEstimatedCost(response));
      setMessage("Parsed products are ready for review.");
    } catch (error) {
      const formatted = formatApiError(error);
      setParseStatus("Parse failed.");
      setMessage(formatted);
    } finally {
      setBusy("");
    }
  }

  async function handleValidate() {
    setBusy("validate");
    setMessage("");
    setEnrichmentStatus("Enriching missing product data...");
    const beforeImages = countRows(includedRows, hasImage);
    const beforeDimensions = countRows(includedRows, (row) => hasComplete3dDimensions(row.Dimensions));
    setLastEndpoint(`${API_BASE || "not configured"}/intake/enrich`);
    try {
      const response = await enrichRows({ rows, useWebEnrichment });
      setRows(response.rows);
      setErrors(response.errors);
      const enrichedRows = response.rows.filter((row) => row.Include !== false);
      const afterImages = countRows(enrichedRows, hasImage);
      const afterDimensions = countRows(enrichedRows, (row) => hasComplete3dDimensions(row.Dimensions));
      const unresolved = countRows(enrichedRows, (row) => missingFieldsForRow(row).length > 0);
      setEnrichmentStats({
        filledImages: Math.max(0, afterImages - beforeImages),
        filledDimensions: Math.max(0, afterDimensions - beforeDimensions),
        unresolved,
      });
      setEstimatedCost(getEstimatedCost(response));
      setEnrichmentStatus(useWebEnrichment ? "Enrichment complete." : "Input updates saved without web enrichment.");
      setEnrichedProductsOpen(false);
      setMessage(useWebEnrichment ? "Missing info search complete." : "Input updates saved without web search.");
    } catch (error) {
      const formatted = formatApiError(error);
      setEnrichmentStatus("Enrichment failed.");
      setMessage(formatted);
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

  async function handleProgramaExport(format: "csv" | "xlsx" | "zip" | "debug") {
    setBusy("export");
    setMessage("");
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
      setMessage("Use this file for Programa Import Products.");
    } catch (error) {
      setMessage(formatApiError(error));
    } finally {
      setBusy("");
    }
  }

  async function handleSendToPrograma() {
    setBusy("programa");
    setProgramaMessage("");
    setMessage("");
    try {
      const response = await sendToPrograma({
        projectName: project,
        scheduleUrl,
        rows: includedRows,
        allowBlankFields: false,
        uploadProductImages: true,
      });
      setProgramaMessage(response.message || `Programa send status: ${response.status}`);
    } catch (error) {
      setProgramaMessage(formatApiError(error));
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
          <div className="flex flex-wrap items-center gap-3">
            <ApiConnectionBadge status={apiStatus} label={apiStatusText} apiBase={API_BASE} />
            <button
              type="button"
              className="btn-secondary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-3 text-sm font-semibold text-charcoal hover:border-orangeBorder hover:bg-orangeSoft/40"
              onClick={() => setSettingsOpen(true)}
            >
              <Settings className="h-4 w-4" />
              <span>Settings</span>
            </button>
          </div>
        </header>

        <div className="grid gap-2 rounded-2xl border border-linen bg-white/72 p-3 sm:grid-cols-5">
          {["Upload", "Parse", "Review", "Enrich", "Export"].map((label, index) => (
            <div key={label} className="flex items-center gap-2 rounded-xl bg-ivory/70 px-3 py-2">
              <span className="grid h-6 w-6 place-items-center rounded-full bg-orangeSoft text-xs font-semibold text-bronze">
                {index + 1}
              </span>
              <span className="text-xs font-semibold uppercase tracking-[0.08em] text-charcoal/60">{label}</span>
            </div>
          ))}
        </div>

        <Panel step="1" title="Upload / Input" subtitle="Add vendor PDFs, quote sheets, tear sheets, receipts, product links, and mass photo uploads.">
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
                title="PDFs, quote sheets, tear sheets, receipts"
                description="Upload vendor PDFs or document captures for parser extraction."
                meta={files.length ? `${files.length} PDF${files.length === 1 ? "" : "s"} selected` : "No PDFs selected"}
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
                title="Mass photo upload"
                description="Create photo-only inventory rows with uploaded product images."
                meta={bulkImages.length ? `${bulkImages.length} image${bulkImages.length === 1 ? "" : "s"} selected` : "No photos selected"}
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
                title="Product URLs / links"
                description="Paste product pages, vendor links, or source URLs one per line."
                meta={`${urls.split(/\r?\n/).filter((url) => url.trim()).length} link${urls.split(/\r?\n/).filter((url) => url.trim()).length === 1 ? "" : "s"} entered`}
              >
                <textarea
                  className="input-surface min-h-28 w-full resize-none rounded-xl p-3 text-sm leading-6 text-charcoal"
                  value={urls}
                  onChange={(event) => setUrls(event.target.value)}
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
                  Add Photo Rows
                </button>
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel step="2" title="Parse" subtitle="Run the parsing system on uploaded PDFs and product links. Enrichment stays off until Step 4.">
          <div className="grid gap-4">
            <div className="rounded-xl border border-linen bg-ivory/70 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-sm font-semibold text-charcoal">{parseStatus}</div>
                  <div className="mt-1 text-xs text-taupe">Endpoint: {lastEndpoint || `${API_BASE || "not configured"}/intake/generate`}</div>
                </div>
                <button
                  className="btn-primary inline-flex h-12 items-center justify-center gap-2 rounded-xl px-6 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
                  disabled={busy === "generate" || (!files.length && !urls.trim())}
                  onClick={handleGenerate}
                >
                  {busy === "generate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                  Parse Uploaded Files
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <StatusBadge value={`${files.length} PDFs`} />
                <StatusBadge value={`${urls.split(/\r?\n/).filter((url) => url.trim()).length} URLs`} />
                <StatusBadge value={useAiPdf ? "AI PDF parsing on" : "AI PDF parsing off"} />
                <StatusBadge value={`${rows.length} products found`} />
              </div>
            </div>
            {message ? <p className="whitespace-pre-wrap rounded-xl border border-linen bg-white px-4 py-3 text-sm text-charcoal/70">{message}</p> : null}
            {errors.length ? <ErrorList errors={errors} /> : null}
          </div>
        </Panel>

        <Panel step="3" title="Review Parsed Products" subtitle="Start with the summary, then expand the parsed product table only when needed.">
          <div className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <SummaryCard label="Products found" value={rows.length} />
              <SummaryCard label="Missing SKU" value={missingSkuCount} tone={missingSkuCount ? "warning" : "ok"} />
              <SummaryCard label="Missing dimensions" value={missingDimensionsCount} tone={missingDimensionsCount ? "warning" : "ok"} />
              <SummaryCard label="Missing image" value={missingImageCount} tone={missingImageCount ? "warning" : "ok"} />
              <SummaryCard label="Missing supplier" value={missingSupplierCount} tone={missingSupplierCount ? "warning" : "ok"} />
            </div>
            <DisclosureButton open={parsedProductsOpen} onClick={() => setParsedProductsOpen((open) => !open)}>
              View parsed products
            </DisclosureButton>
            {parsedProductsOpen ? (
              <ProductTable rows={rows} categories={categories} sections={sections} updateRow={updateRow} openVendorCall={openVendorCall} />
            ) : null}
          </div>
        </Panel>

        <Panel step="4" title="Enrich Missing Data" subtitle="Use the backend enrichment route only when you choose to search for missing fields.">
          <div className="grid gap-4">
            <div className="rounded-xl border border-linen bg-ivory/70 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-sm font-semibold text-charcoal">{enrichmentStatus}</div>
                  <div className="mt-1 text-xs text-taupe">Estimated cost: {estimatedCost}</div>
                </div>
                <button
                  className="btn-primary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                  disabled={busy === "validate" || rows.length === 0}
                  onClick={handleValidate}
                >
                  {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                  Enrich Products
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <StatusBadge value={`${enrichmentStats.filledImages} images filled`} />
                <StatusBadge value={`${enrichmentStats.filledDimensions} dimensions filled`} />
                <StatusBadge value={`${enrichmentStats.unresolved || unresolvedCount} unresolved`} />
                <StatusBadge value={useWebEnrichment ? "Web enrichment on" : "Web enrichment off"} />
              </div>
            </div>

            {rows.length > 0 && missingInputRows.length ? (
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
            ) : rows.length > 0 ? (
              <div className="flex items-center gap-2 rounded-xl border border-sage/20 bg-sage/10 p-4 text-sm text-sage">
                <CheckCircle2 className="h-4 w-4" />
                No missing details.
              </div>
            ) : (
              <p className="text-sm text-taupe">Create product entries first.</p>
            )}
            {errors.length ? <ErrorList errors={errors} /> : null}
          </div>
        </Panel>

        <Panel step="5" title="Review Enriched Products" subtitle="Confirm readiness after enrichment before exporting.">
          <div className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
              <SummaryCard label="Products ready" value={readyRows} tone={readyRows ? "ok" : "neutral"} />
              <SummaryCard label="Needs review" value={needsReview} tone={needsReview ? "warning" : "ok"} />
              <SummaryCard label="Ignored" value={ignored} />
              <SummaryCard label="Images found" value={imagesFoundCount} />
              <SummaryCard label="Dimensions found" value={dimensionsFoundCount} />
              <SummaryCard label="Est. cost" value={estimatedCost} />
            </div>
            <DisclosureButton open={enrichedProductsOpen} onClick={() => setEnrichedProductsOpen((open) => !open)}>
              View enriched products
            </DisclosureButton>
            {enrichedProductsOpen ? (
              <ProductTable rows={rows} categories={categories} sections={sections} updateRow={updateRow} openVendorCall={openVendorCall} />
            ) : null}
          </div>
        </Panel>

        <Panel step="6" title="Export" subtitle="Download the Programa-ready workbook first. CSV, ZIP, debug, and direct send are secondary.">
          <div className="grid gap-4">
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
                  onClick={() => handleProgramaExport("debug")}
                >
                  <FileText className="h-4 w-4" />
                  Export Debug Report
                </button>
                <button
                  className="btn-secondary inline-flex h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold text-taupe hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                  disabled={!programaSendEnabled || busy === "programa" || exportSummary.export_count === 0 || !scheduleUrl.trim()}
                  onClick={handleSendToPrograma}
                  title={programaSendEnabled ? "Send approved rows to Programa" : "Direct Programa send is disabled unless the integration is configured."}
                >
                  {busy === "programa" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                  Send to Programa
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusBadge value={`${exportSummary.export_count} export-ready`} />
              <StatusBadge value={`Images ${exportSummary.image_url_present}/${exportSummary.image_url_total}`} />
              <StatusBadge value={`${exportSummary.missing_section.length} missing section`} />
              {programaSendEnabled ? <StatusBadge value="Programa send configured" /> : <StatusBadge value="Programa send not configured" />}
            </div>
            {programaMessage ? <p className="whitespace-pre-wrap rounded-xl border border-linen bg-white px-4 py-3 text-sm text-charcoal/70">{programaMessage}</p> : null}
            {exportSummary.missing_image_url ||
            exportSummary.missing_dimensions ||
            exportSummary.rejected_product_urls.length ||
            exportSummary.pdf_product_urls.length ||
            exportSummary.suspicious_dimensions_rejected.length ? (
              <div className="rounded-xl border border-orangeBorder bg-orangeSoft/40 px-4 py-3 text-sm text-bronze">
                {exportSummary.missing_image_url ? <div>{exportSummary.missing_image_url} row(s) missing image URLs.</div> : null}
                {exportSummary.missing_dimensions ? <div>{exportSummary.missing_dimensions} row(s) missing dimensions.</div> : null}
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
        {settingsOpen ? (
          <SettingsDialog
            buildInfo={buildInfo}
            useAiPdf={useAiPdf}
            useWebEnrichment={useWebEnrichment}
            scheduleUrl={scheduleUrl}
            onUseAiPdfChange={setUseAiPdf}
            onUseWebEnrichmentChange={setUseWebEnrichment}
            onScheduleUrlChange={setScheduleUrl}
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
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-linen pt-4 text-[11px] font-medium uppercase tracking-[0.12em] text-taupe">
          <span>Frontend v{buildInfo.version}</span>
          <span>Commit {buildInfo.commit}</span>
          <span>Built {formatBuildTime(buildInfo.builtAt)}</span>
        </footer>
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

function ApiConnectionBadge({
  status,
  label,
  apiBase,
}: {
  status: "checking" | "online" | "offline";
  label: string;
  apiBase: string;
}) {
  const tone =
    status === "online"
      ? "border-sage/20 bg-sage/10 text-sage"
      : status === "offline"
        ? "border-clay/20 bg-clay/10 text-clay"
        : "border-orangeBorder bg-orangeSoft text-bronze";

  return (
    <div className={`max-w-full rounded-xl border px-3 py-2 text-xs ${tone}`} title={apiBase || "API base URL not configured"}>
      <div className="font-semibold">{status === "online" ? "Backend connected" : status === "offline" ? "Backend offline" : "Checking backend"}</div>
      <div className="max-w-[280px] truncate opacity-80">{label}</div>
    </div>
  );
}

function InputCard({
  icon,
  title,
  description,
  meta,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  meta: string;
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
          <p className="mt-1 text-sm leading-5 text-taupe">{description}</p>
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

function ErrorList({ errors }: { errors: string[] }) {
  return (
    <div className="rounded-xl border border-clay/20 bg-clay/10 px-4 py-3 text-sm text-clay">
      {errors.map((error) => (
        <div key={error}>{error}</div>
      ))}
    </div>
  );
}

function ProductTable({
  rows,
  categories,
  sections,
  updateRow,
  openVendorCall,
}: {
  rows: IntakeRow[];
  categories: string[];
  sections: string[];
  updateRow: (index: number, key: string, value: unknown) => void;
  openVendorCall: (row: IntakeRow, missingFields: string[]) => void;
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
  );
}

function ReviewValue({ value }: { value: string }) {
  return value ? <span className="text-charcoal">{value}</span> : <span className="font-semibold text-clay">Missing</span>;
}

function SettingsDialog({
  buildInfo,
  useAiPdf,
  useWebEnrichment,
  scheduleUrl,
  onUseAiPdfChange,
  onUseWebEnrichmentChange,
  onScheduleUrlChange,
  onClose,
}: {
  buildInfo: BuildInfo;
  useAiPdf: boolean;
  useWebEnrichment: boolean;
  scheduleUrl: string;
  onUseAiPdfChange: (value: boolean) => void;
  onUseWebEnrichmentChange: (value: boolean) => void;
  onScheduleUrlChange: (value: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end bg-charcoal/28 px-4 py-5 backdrop-blur-sm sm:px-7">
      <div className="w-full max-w-md origin-top-right rounded-2xl border border-linen bg-white p-5 shadow-xl">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-charcoal">Settings</h2>
            <p className="mt-1 text-sm text-taupe">Internal intake preferences.</p>
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
          <Field label="Programa Schedule URL">
            <input
              className="input-surface mt-3 h-10 w-full rounded-xl px-3 text-sm text-charcoal"
              value={scheduleUrl}
              onChange={(event) => onScheduleUrlChange(event.target.value)}
              placeholder="https://app.programa.design/schedules2/schedules/..."
            />
          </Field>
        </div>

        <div className="mt-4 rounded-xl border border-linen bg-white p-4">
          <h3 className="text-sm font-semibold text-charcoal">Build Version</h3>
          <dl className="mt-3 grid gap-2 text-xs text-taupe">
            <div className="flex items-center justify-between gap-3">
              <dt>Frontend</dt>
              <dd className="font-mono text-charcoal">v{buildInfo.version}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt>Commit</dt>
              <dd className="font-mono text-charcoal">{buildInfo.commit}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt>Built</dt>
              <dd className="text-right font-mono text-charcoal">{formatBuildTime(buildInfo.builtAt)}</dd>
            </div>
            {buildInfo.deploymentUrl ? (
              <div className="grid gap-1">
                <dt>Deployment</dt>
                <dd className="break-all font-mono text-charcoal">{buildInfo.deploymentUrl}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      </div>
    </div>
  );
}

function Panel({
  step,
  title,
  subtitle,
  children,
}: {
  step?: string;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-linen bg-white/72 p-5 sm:p-6">
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
