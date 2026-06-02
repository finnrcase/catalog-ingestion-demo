export type IntakeRow = {
  Include?: boolean;
  "Confidence Score"?: number;
  "Review Required"?: boolean;
  "Suggested Action"?: string;
  Project?: string;
  Room?: string;
  "Product Name"?: string;
  Brand?: string;
  Dimensions?: string;
  Quantity?: number | string;
  Supplier?: string;
  "Finish / Color"?: string;
  "Product Category"?: string;
  "Model/SKU"?: string;
  Notes?: string;
  "Product URL"?: string;
  "Source Type"?: string;
  Status?: string;
  [key: string]: unknown;
};

export type IntakeResponse = {
  rows: IntakeRow[];
  errors: string[];
  eligible_count: number;
  blocked_count: number;
  dimension_diagnostics?: Record<string, unknown>[];
  stage_timings?: Record<string, unknown>;
  estimated_cost?: number | string;
  cost_estimate?: number | string;
};

export type SchemaResponse = {
  categories: string[];
  sections: string[];
  statuses: string[];
  reviewFields: string[];
};

export type IntegrationsStatus = {
  openai?: {
    provider?: string;
    status?: "Connected" | "Not Configured" | string;
    configured?: boolean;
    model?: string;
    further_enrichment_supported?: boolean;
  };
  further_enrichment?: {
    available?: boolean;
    default_enabled?: boolean;
    requires?: string[];
  };
  [key: string]: unknown;
};

export type StoredProductSource = {
  id?: string;
  normalized_brand?: string;
  normalized_model_sku?: string;
  display_brand?: string;
  display_model_sku?: string;
  brand?: string;
  model_sku?: string;
  product_name?: string;
  product_page_url?: string;
  manufacturer_url?: string;
  spec_sheet_url?: string;
  image_url?: string;
  dimension_source_url?: string;
  image_source_url?: string;
  dimensions_text?: string;
  dimensions?: string;
  width_in?: string | number;
  height_in?: string | number;
  depth_in?: string | number;
  source_domain?: string;
  source_type?: string;
  confidence_score?: number;
  confidence?: string;
  dimension_confidence?: string;
  image_confidence?: string;
  success_count?: number;
  failure_count?: number;
  last_verified_at?: string;
  first_seen_at?: string;
  notes?: string;
  [key: string]: unknown;
};

export type PreferredSourceDomain = {
  id?: string;
  domain?: string;
  source_type?: string;
  success_count?: number;
  failure_count?: number;
  downranked?: boolean;
  last_success_at?: string;
  last_failure_at?: string;
  notes?: string;
  [key: string]: unknown;
};

export type StoredSourcesResponse = {
  storage_backend: string;
  sources: StoredProductSource[];
};

export type PreferredDomainsResponse = {
  storage_backend: string;
  domains: PreferredSourceDomain[];
};

export type ProgramaExportValidation = {
  skipped: { index: number; product_name: string }[];
  missing_section: { index: number; product_name: string }[];
  missing_dimensions: number;
  missing_product_url: number;
  missing_image_url: number;
  image_url_present: number;
  image_url_total: number;
  export_count: number;
  unique_sections: string[];
  section_counts: Record<string, number>;
  section_equals_product_name: { index: number; product_name: string; section: string }[];
  section_too_long: { index: number; product_name: string; section: string }[];
  too_many_unique_sections: boolean;
  canonical_sections: string[];
  duplicates_removed: { index: number; product_name: string; brand?: string; sku?: string; kept_index?: number; reason?: string }[];
  duplicate_rows_removed: number;
  suspicious_dimensions_rejected: { index: number; product_name: string; brand?: string; sku?: string; dimensions?: string; reason?: string }[];
  rejected_product_urls: { index: number; product_name: string; brand?: string; sku?: string; url?: string; reason?: string }[];
  pdf_product_urls: { index: number; product_name: string; brand?: string; sku?: string; url?: string; reason?: string }[];
  blank_price_only_rows: { index: number; price?: string; reason?: string }[];
  missing_model_manufacturer: { index: number; product_name: string; brand?: string; sku?: string; reason?: string }[];
  phone_email_header_contamination: { index: number; product_name: string; brand?: string; sku?: string; reason?: string }[];
  parsed_rows_count: number;
  export_rows_count: number;
  readiness_score?: number;
  readiness_status?: string;
  readiness_missing_fields?: Record<string, number>;
};

export type VendorCallResponse = {
  status: string;
  script: string;
  payload: Record<string, unknown>;
};

export type VendorCallStatus = {
  enabled: boolean;
  provider: string;
  api_key_configured?: boolean;
  agent_name?: string;
  self_test_phone_configured?: boolean;
  message?: string;
};

export type VendorCallStartResponse = {
  status: string;
  message?: string;
  call_id?: string | null;
  provider?: string;
  agent_name?: string;
  task?: string;
  record_path?: string;
};

export type VendorCallRefreshResponse = {
  status: string;
  message?: string;
  call_id?: string | null;
  provider?: string;
  provider_status?: string;
  queue_status?: string;
  completed?: boolean;
  answered_by?: string;
  summary?: string;
  transcript?: string;
  recording_url?: string;
  extracted_values?: Record<string, string>;
  confidence?: number;
  review_required?: boolean;
};
