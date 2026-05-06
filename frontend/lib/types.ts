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
};

export type SchemaResponse = {
  categories: string[];
  sections: string[];
  statuses: string[];
  reviewFields: string[];
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
