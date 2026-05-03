"use client";

import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Download,
  FileCheck2,
  FileText,
  ImageIcon,
  Loader2,
  Phone,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  exportProgramaCsv,
  exportProgramaDebugCsv,
  exportProgramaXlsx,
  fetchSchema,
  fetchVendorCallStatus,
  generateIntakeTable,
  generateVendorCallScript,
  refreshVendorCall,
  startVendorCall,
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
  Supplier: "Supplier",
  Location: "Room",
  Category: "Product Category",
};

const missingFieldPlaceholders: Record<string, string> = {
  "Product Name": "Enter product name",
  Brand: "Enter manufacturer / brand",
  Dimensions: "Enter full W x H x D dimensions",
  Quantity: "Enter quantity",
  Supplier: "Enter supplier",
  Location: "Enter location",
  Category: "Enter category",
};

function rowText(row: IntakeRow, key: string) {
  return String(row[key] ?? "");
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
  const [uploadError, setUploadError] = useState("");
  const [useAiPdf, setUseAiPdf] = useState(true);
  const [debugMode, setDebugMode] = useState(false);
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"generate" | "validate" | "vendorCall" | "export" | "">("");
  const [exportSummary, setExportSummary] = useState({
    skipped: [] as { index: number; product_name: string }[],
    missing_section: [] as { index: number; product_name: string }[],
    missing_dimensions: 0,
    missing_product_url: 0,
    missing_image_url: 0,
    export_count: 0,
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
    fetchSchema()
      .then((schema) => setCategories(schema.categories))
      .catch(() => setCategories([]));
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

  useEffect(() => {
    if (includedRows.length === 0) {
      setExportSummary({
        skipped: [],
        missing_section: [],
        missing_dimensions: 0,
        missing_product_url: 0,
        missing_image_url: 0,
        export_count: 0,
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
  }, [includedRows]);

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
  }

  function clearBulkImages() {
    setBulkImages([]);
    setBulkImageError("");
    if (bulkImageInputRef.current) bulkImageInputRef.current.value = "";
  }

  async function handleGenerate() {
    setBusy("generate");
    setMessage("");
    setErrors([]);
    try {
      const response = await generateIntakeTable({ project, room, urls, useAiPdf, files });
      setRows(response.rows);
      setErrors(response.errors);
      setMessage("Intake table is ready for review.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not generate the intake table.");
    } finally {
      setBusy("");
    }
  }

  async function handleValidate() {
    setBusy("validate");
    try {
      const response = await validateRows(rows);
      setRows(response.rows);
      setErrors(response.errors);
      setMessage("Input updates saved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save input updates.");
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

  async function handleProgramaExport(format: "csv" | "xlsx" | "debug-csv") {
    setBusy("export");
    try {
      const safeProject = project.trim().replaceAll(" ", "_") || "sch";
      const blob =
        format === "xlsx"
          ? await exportProgramaXlsx(includedRows)
          : format === "debug-csv"
            ? await exportProgramaDebugCsv(includedRows)
            : await exportProgramaCsv(includedRows);
      const suffix = format === "xlsx" ? "xlsx" : "csv";
      const label = format === "debug-csv" ? "programa_debug" : "programa_import";
      downloadBlob(blob, `${safeProject}_${label}.${suffix}`);
      setMessage("Programa import file downloaded.");
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
    <main className="min-h-screen px-5 py-6 sm:px-8 lg:px-10">
      <div className="mx-auto flex max-w-[1500px] flex-col gap-5">
        <header className="flex flex-col gap-5 rounded-lg border border-linen bg-paper px-6 py-5 shadow-panel backdrop-blur md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-5">
            <LogoMark />
            <span className="rounded-full border border-orangeBorder bg-orangeSoft px-3 py-1 text-xs font-medium text-bronze">
              Internal
            </span>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="grid grid-cols-3 gap-2 text-center">
              <Metric label="Export Rows" value={readyRows} tone="ready" />
              <Metric label="Needs Review" value={needsReview} tone="warn" />
              <Metric label="Ignored" value={ignored} tone="muted" />
            </div>
            <div className="flex flex-col items-stretch gap-2">
              <button
                className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-bronze bg-bronze px-5 text-sm font-semibold text-white shadow-sm transition hover:border-orangeHover hover:bg-orangeHover disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
                disabled={busy === "export" || exportSummary.export_count === 0}
                onClick={() => handleProgramaExport("csv")}
              >
                {busy === "export" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Download Programa CSV
              </button>
              <p className="text-center text-xs font-medium text-taupe">
                Use Programa&apos;s native Import Products tool for most imports.
              </p>
            </div>
          </div>
        </header>

        <section className="grid gap-5">
          <Panel title="Project Setup" subtitle="Set default metadata for parsed rows. No Programa URL is required.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Default Room / Location">
                <input
                  className="h-11 w-full rounded-lg border border-linen bg-white px-3 text-sm text-charcoal shadow-sm"
                  value={room}
                  onChange={(event) => setRoom(event.target.value)}
                  placeholder="Kitchen"
                />
              </Field>
              <Field label="Display Name (optional)">
                <input
                  className="h-11 w-full rounded-lg border border-linen bg-white px-3 text-sm text-charcoal shadow-sm"
                  value={project}
                  onChange={(event) => setProject(event.target.value)}
                  placeholder="For file names and review context"
                />
              </Field>
            </div>
          </Panel>

          <Panel title="Product Intake" subtitle="Paste product links, upload product images, or add PDFs when needed.">
            <div className="grid gap-5">
              <Field label="Product URLs">
                <textarea
                  className="min-h-[150px] w-full resize-none rounded-lg border border-linen bg-white p-3 text-sm text-charcoal shadow-sm"
                  value={urls}
                  onChange={(event) => setUrls(event.target.value)}
                  placeholder={"Paste one URL per line\nhttps://www.vendor.com/product"}
                />
              </Field>

              <section>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-charcoal">Bulk Image Upload</h3>
                    <p className="mt-1 text-xs text-taupe">
                      Upload many product photos now. They will stay in frontend state for the future backend handoff.
                    </p>
                  </div>
                  {bulkImages.length > 0 ? (
                    <button
                      type="button"
                      className="rounded-lg border border-linen bg-white px-3 py-2 text-xs font-semibold text-taupe shadow-sm transition hover:bg-ivory hover:text-charcoal"
                      onClick={clearBulkImages}
                    >
                      Clear all images
                    </button>
                  ) : null}
                </div>
                <label
                  className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-6 text-center shadow-sm transition ${
                    isImageDragActive
                      ? "border-orangeBorder bg-orangeSoft"
                      : "border-linen bg-white hover:border-orangeBorder hover:bg-orangeSoft/50"
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
                  <div className="grid h-11 w-11 place-items-center rounded-full bg-orangeSoft text-bronze">
                    <ImageIcon className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-charcoal">Drop product images here</div>
                    <div className="mt-1 text-xs text-taupe">or click to choose JPG, PNG, or WebP files</div>
                  </div>
                  <input
                    ref={bulkImageInputRef}
                    className="hidden"
                    type="file"
                    accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                    multiple
                    onChange={(event) => handleBulkImageSelection(event.target.files)}
                  />
                </label>
                {bulkImageError ? (
                  <div className="mt-3 rounded-lg border border-clay/20 bg-clay/10 px-3 py-2 text-sm text-clay">
                    {bulkImageError}
                  </div>
                ) : null}
                {bulkImages.length > 0 ? (
                  <div className="mt-4">
                    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-medium text-taupe">
                      <span className="rounded-full border border-orangeBorder bg-orangeSoft px-3 py-1 text-bronze">
                        {bulkImages.length} image{bulkImages.length === 1 ? "" : "s"} uploaded
                      </span>
                      {bulkImages.length > 100 ? (
                        <span className="rounded-full border border-orangeBorder bg-white px-3 py-1 text-bronze">
                          Large upload: previews are limited for performance
                        </span>
                      ) : null}
                    </div>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                      {bulkImagePreviews.slice(0, 12).map(({ file, url }) => (
                        <figure
                          key={`${file.name}-${file.size}-${file.lastModified}`}
                          className="overflow-hidden rounded-lg border border-linen bg-white shadow-sm"
                        >
                          <img src={url} alt={file.name} className="h-24 w-full object-cover" />
                          <figcaption className="truncate px-2 py-2 text-[11px] text-taupe">{file.name}</figcaption>
                        </figure>
                      ))}
                    </div>
                    {bulkImages.length > 12 ? (
                      <p className="mt-2 text-xs text-taupe">+ {bulkImages.length - 12} more image previews hidden</p>
                    ) : null}
                  </div>
                ) : null}
              </section>

              <div className="rounded-lg border border-dashed border-linen bg-ivory p-4">
                <label className="flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-transparent py-2 text-center transition hover:border-orangeBorder hover:bg-orangeSoft">
                  <Upload className="h-6 w-6 text-bronze" />
                  <span className="text-sm font-medium text-charcoal">PDF Upload</span>
                  <span className="text-xs text-taupe">{files.length ? "Choose more files or replace selection" : "Choose PDF files"}</span>
                  <input
                    ref={fileInputRef}
                    className="hidden"
                    type="file"
                    accept="application/pdf"
                    multiple
                    onChange={(event) => handleFileSelection(event.target.files)}
                  />
                </label>
                {uploadError ? (
                  <div className="mt-3 rounded-lg border border-clay/20 bg-clay/10 px-3 py-2 text-sm text-clay">
                    {uploadError}
                  </div>
                ) : null}
                {files.length > 0 ? (
                  <div className="mt-4 space-y-2">
                    {files.map((file, index) => (
                      <div
                        key={`${file.name}-${file.size}-${file.lastModified}`}
                        className="flex items-center justify-between gap-3 rounded-lg border border-orangeBorder/70 bg-white px-3 py-2 shadow-sm"
                      >
                        <div className="flex min-w-0 items-center gap-3">
                          <FileCheck2 className="h-4 w-4 shrink-0 text-bronze" />
                          <div className="min-w-0">
                            <div className="truncate text-sm font-medium text-charcoal">{file.name}</div>
                            <div className="text-xs text-taupe">{formatFileSize(file.size)}</div>
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <span className="rounded-full bg-orangeSoft px-2.5 py-1 text-xs font-semibold text-bronze">
                            Uploaded
                          </span>
                          <button
                            type="button"
                            className="grid h-8 w-8 place-items-center rounded-lg border border-linen bg-white text-taupe transition hover:border-orangeBorder hover:bg-orangeSoft hover:text-charcoal"
                            aria-label={`Remove ${file.name}`}
                            onClick={() => removeFile(index)}
                          >
                            <X className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
                <label className="mt-4 flex items-center gap-2 text-sm text-taupe">
                  <input
                    type="checkbox"
                    checked={useAiPdf}
                    onChange={(event) => setUseAiPdf(event.target.checked)}
                    className="h-4 w-4 accent-bronze"
                  />
                  Use AI to interpret uploaded PDFs
                </label>
              </div>
            </div>
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-bronze bg-bronze px-5 text-sm font-semibold text-white shadow-sm transition hover:border-orangeHover hover:bg-orangeHover disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
                disabled={busy === "generate"}
                onClick={handleGenerate}
              >
                {busy === "generate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                Generate Intake Table
              </button>
              {message ? <p className="text-sm text-charcoal/65">{message}</p> : null}
            </div>
          </Panel>
        </section>

        {errors.length ? (
          <div className="rounded-lg border border-clay/20 bg-clay/10 px-4 py-3 text-sm text-clay">
            {errors.map((error) => (
              <div key={error}>{error}</div>
            ))}
          </div>
        ) : null}

        {rows.length > 0 ? (
          <>
            <Panel
              title="Missing Inputs"
              subtitle="Fill any details you have now, or use the phone button to prepare a vendor call."
            >
              {missingInputRows.length ? (
                <div className="space-y-3">
                  {rows.map((row, index) => {
                    const missingFields = row.Include !== false ? missingFieldsForRow(row) : [];
                    return missingFields.length > 0 ? (
                      <div key={index} className="grid gap-3 rounded-lg border border-linen bg-ivory/55 p-3 md:grid-cols-[1.1fr_1.2fr] md:items-start">
                        <div>
                          <div className="text-sm font-semibold text-charcoal">{rowText(row, "Product Name") || "Unnamed Item"}</div>
                          <div className="text-xs text-charcoal/55">
                            {[rowText(row, "Brand"), rowText(row, "Model/SKU")].filter(Boolean).join(" / ") || "Product details pending"}
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
                  <button
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-bronze/25 bg-bronze/10 px-4 text-sm font-semibold text-bronze hover:bg-bronze/15"
                    disabled={busy === "validate"}
                    onClick={handleValidate}
                  >
                    {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    Save Input Updates
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2 text-sm text-sage">
                  <CheckCircle2 className="h-4 w-4" />
                  All included rows have the key inputs filled.
                </div>
              )}
            </Panel>

            <Panel
              title="Export for Programa Import"
              subtitle="Download a Programa-compatible CSV or XLSX file, then upload it through Programa's Import Products tool."
            >
              <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
                <div className="rounded-lg border border-linen bg-ivory p-4">
                  <h3 className="text-sm font-semibold text-charcoal">Validation Summary</h3>
                  <div className="mt-3 grid gap-2 text-sm text-charcoal/75 sm:grid-cols-2">
                    <SummaryLine label="Rows ready for export" value={exportSummary.export_count} tone="ready" />
                    <SummaryLine label="Skipped: missing Product Name" value={exportSummary.skipped.length} tone="warn" />
                    <SummaryLine label="Missing Section" value={exportSummary.missing_section.length} tone="warn" />
                    <SummaryLine label="Missing Dimensions" value={exportSummary.missing_dimensions} tone="warn" />
                    <SummaryLine label="Missing Product URL" value={exportSummary.missing_product_url} tone="warn" />
                    <SummaryLine label="Missing Image URL" value={exportSummary.missing_image_url} tone="warn" />
                  </div>
                  {exportSummary.skipped.length > 0 ? (
                    <p className="mt-3 text-xs font-medium text-bronze">
                      Rows without Product Name are excluded from the Programa import file.
                    </p>
                  ) : null}
                </div>
                <div className="rounded-lg border border-linen bg-white p-4">
                  <h3 className="text-sm font-semibold text-charcoal">Download Files</h3>
                  <p className="mt-1 text-sm leading-6 text-taupe">
                    Legacy direct-upload workflow is deprecated. Use CSV/XLSX export for most imports.
                  </p>
                  <div className="mt-4 grid gap-2">
                    <button
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-bronze bg-bronze px-4 text-sm font-semibold text-white shadow-sm hover:bg-orangeHover disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze"
                      disabled={busy === "export" || exportSummary.export_count === 0}
                      onClick={() => handleProgramaExport("csv")}
                    >
                      {busy === "export" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                      Download Programa CSV
                    </button>
                    <button
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-charcoal/15 bg-white px-4 text-sm font-semibold text-charcoal shadow-sm hover:bg-ivory disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/60"
                      disabled={busy === "export" || exportSummary.export_count === 0}
                      onClick={() => handleProgramaExport("xlsx")}
                    >
                      <Download className="h-4 w-4" />
                      Download Programa XLSX
                    </button>
                    <label className="mt-1 flex items-center gap-2 text-xs font-medium text-taupe">
                      <input
                        type="checkbox"
                        checked={debugMode}
                        onChange={(event) => setDebugMode(event.target.checked)}
                        className="h-4 w-4 accent-bronze"
                      />
                      Enable debug export
                    </label>
                    {debugMode ? (
                      <button
                        className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-linen bg-ivory px-4 text-sm font-semibold text-taupe shadow-sm hover:bg-white disabled:cursor-not-allowed disabled:text-taupe/60"
                        disabled={busy === "export" || exportSummary.export_count === 0}
                        onClick={() => handleProgramaExport("debug-csv")}
                      >
                        <Download className="h-4 w-4" />
                        Download Debug CSV
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            </Panel>

            <Panel title="Review Table" subtitle="Review each row before exporting. Missing inputs stay flagged.">
              <div className="overflow-x-auto">
                <table className="min-w-[1180px] w-full border-separate border-spacing-0 text-left text-sm">
                  <thead>
                    <tr>
                      {reviewColumns.map((column) => (
                        <th key={column} className="border-b border-linen bg-ivory/70 px-3 py-3 text-xs font-semibold text-charcoal/60">
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
                      <tr key={index} className="align-top">
                        {reviewColumns.map((column) => (
                          <td key={column} className="border-b border-linen/70 px-3 py-2">
                            <Cell
                              row={row}
                              column={column}
                              categories={categories}
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
              <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2 text-sm text-charcoal/60">
                  <AlertTriangle className="h-4 w-4 text-bronze" />
                  {readyRows} ready for export. {missingInputRows.length} have missing inputs.
                </div>
              </div>
            </Panel>
          </>
        ) : (
          <div className="rounded-lg border border-linen bg-paper/80 px-5 py-12 text-center shadow-panel">
            <ArrowUpRight className="mx-auto mb-3 h-5 w-5 text-bronze" />
            <p className="text-sm text-charcoal/60">Add PDFs or product URLs to begin.</p>
          </div>
        )}
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

function Metric({ label, value, tone }: { label: string; value: number; tone: "ready" | "warn" | "muted" }) {
  const colors = {
    ready: "text-sage bg-sage/10 border-sage/15",
    warn: "text-bronze bg-bronze/10 border-bronze/15",
    muted: "text-charcoal/55 bg-charcoal/5 border-charcoal/10",
  };
  return (
    <div className={`min-w-24 rounded-lg border px-3 py-2 ${colors[tone]}`}>
      <div className="text-lg font-semibold leading-none">{value}</div>
      <div className="mt-1 text-[11px] font-medium">{label}</div>
    </div>
  );
}

function SummaryLine({ label, value, tone }: { label: string; value: number; tone: "ready" | "warn" }) {
  const valueClass = tone === "ready" ? "text-sage" : value > 0 ? "text-bronze" : "text-charcoal/50";
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-linen bg-white px-3 py-2">
      <span>{label}</span>
      <span className={`font-semibold ${valueClass}`}>{value}</span>
    </div>
  );
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-linen/90 bg-paper/90 p-5 shadow-panel backdrop-blur">
      <div className="mb-4">
        <h2 className="text-base font-semibold text-charcoal">{title}</h2>
        <p className="mt-1 text-sm text-charcoal/55">{subtitle}</p>
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
      <div className="w-full max-w-xl rounded-lg border border-linen bg-white p-5 shadow-panel">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-charcoal">Call Vendor</h2>
            <p className="mt-1 text-sm text-taupe">Prepare a script to retrieve missing product details.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-lg border border-linen bg-white text-taupe hover:bg-ivory"
            aria-label="Close vendor call dialog"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-5 grid gap-3 rounded-lg border border-linen bg-ivory p-3 text-sm">
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
              className="h-10 w-full rounded-lg border border-linen bg-white px-3 text-sm shadow-sm"
              value={state.phoneNumber}
              onChange={(event) => onChange({ ...state, phoneNumber: event.target.value })}
              placeholder="Vendor phone number"
            />
          </Field>
          <Field label="Call Goal">
            <textarea
              className="min-h-20 w-full resize-none rounded-lg border border-linen bg-white px-3 py-2 text-sm shadow-sm"
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
            className="inline-flex h-10 items-center justify-center rounded-lg border border-bronze bg-bronze px-4 text-sm font-semibold text-white shadow-sm hover:bg-orangeHover disabled:cursor-not-allowed disabled:border-orangeBorder disabled:bg-orangeSoft disabled:text-bronze disabled:shadow-none"
          >
            {busy ? "Generating..." : "Generate Call Script"}
          </button>
          <div className="text-center sm:text-left">
            <button
              type="button"
              disabled={!providerEnabled || !state.phoneNumber.trim() || !state.script || startingCall}
              onClick={handleStartCall}
              className="inline-flex h-10 items-center justify-center rounded-lg border border-linen bg-ivory px-4 text-sm font-semibold text-taupe hover:bg-white disabled:cursor-not-allowed disabled:bg-ivory disabled:text-taupe/65"
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
          className="h-10 min-w-0 flex-1 rounded-lg border border-linen bg-white px-3 text-sm shadow-sm"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={missingFieldPlaceholders[field]}
          type={field === "Quantity" ? "number" : "text"}
        />
        <VendorCallButton onClick={onVendorCall} />
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
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-linen bg-white text-taupe shadow-sm transition hover:bg-ivory hover:text-bronze"
    >
      <Phone className="h-3.5 w-3.5" />
    </button>
  );
}

function Cell({
  row,
  column,
  categories,
  onChange,
  onVendorCall,
}: {
  row: IntakeRow;
  column: string;
  categories: string[];
  onChange: (value: unknown) => void;
  onVendorCall: (missingFields: string[]) => void;
}) {
  const showVendorCall = isColumnMissing(row, column);
  const callField = callFieldLabels[column] || column;
  const vendorCallButton = showVendorCall ? (
    <VendorCallButton onClick={() => onVendorCall([callField])} />
  ) : null;

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

  if (column === "Product Category") {
    return (
      <div className="flex items-start gap-2">
        <select
          className="h-9 w-36 rounded-lg border border-linen bg-white px-2 text-sm"
          value={rowText(row, column)}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Select</option>
          {categories.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>
        {vendorCallButton}
      </div>
    );
  }

  if (column === "Suggested Action" || column === "Notes") {
    const value = column === "Notes" ? cleanNotes(rowText(row, column)) : rowText(row, column);
    return (
      <div className="flex items-start gap-2">
        <textarea
          className="min-h-9 w-56 resize-none rounded-lg border border-linen bg-white px-2 py-2 text-sm"
          value={value}
          onChange={(event) => onChange(column === "Notes" ? cleanNotes(event.target.value) : event.target.value)}
        />
        {vendorCallButton}
      </div>
    );
  }

  const disabled = column === "Project";
  const width = column === "Product Name" ? "w-56" : "w-40";
  return (
    <div className="flex items-start gap-2">
      <input
        className={`${width} h-9 rounded-lg border border-linen bg-white px-2 text-sm disabled:bg-ivory disabled:text-charcoal/50`}
        value={rowText(row, column)}
        disabled={disabled}
        onChange={(event) => onChange(column === "Quantity" ? Number(event.target.value) : event.target.value)}
      />
      {vendorCallButton}
    </div>
  );
}
