-- Durable SCH enrichment memory.
-- Apply this in Supabase before enabling source memory in production.

create extension if not exists pgcrypto;

create table if not exists public.stored_product_sources (
  id uuid primary key default gen_random_uuid(),
  normalized_brand text not null,
  normalized_model_sku text not null,
  normalized_model text,
  display_brand text,
  display_model_sku text,
  brand text,
  model_sku text,
  product_name text,
  manufacturer_url text,
  dimensions_text text,
  dimensions text,
  dimension_source_url text,
  image_source_url text,
  product_page_url text,
  spec_sheet_url text,
  width_in text,
  height_in text,
  depth_in text,
  image_url text,
  source_domain text,
  confidence_score integer default 0,
  confidence text,
  dimension_confidence text,
  image_confidence text,
  source_type text default 'other',
  last_verified_at timestamptz default now(),
  first_seen_at timestamptz default now(),
  success_count integer default 0,
  failure_count integer default 0,
  notes text,
  raw jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (normalized_brand, normalized_model_sku)
);

create index if not exists stored_product_sources_brand_model_idx
  on public.stored_product_sources (normalized_brand, normalized_model_sku);

create index if not exists stored_product_sources_legacy_brand_model_idx
  on public.stored_product_sources (normalized_brand, normalized_model);

create index if not exists stored_product_sources_domain_idx
  on public.stored_product_sources (source_domain);

create index if not exists stored_product_sources_type_idx
  on public.stored_product_sources (source_type);

alter table public.stored_product_sources
  add column if not exists normalized_model_sku text,
  add column if not exists normalized_model text,
  add column if not exists display_brand text,
  add column if not exists display_model_sku text,
  add column if not exists manufacturer_url text,
  add column if not exists dimensions_text text,
  add column if not exists dimension_confidence text,
  add column if not exists image_confidence text,
  add column if not exists first_seen_at timestamptz default now();

update public.stored_product_sources
set
  normalized_model_sku = coalesce(nullif(normalized_model_sku, ''), normalized_model),
  display_brand = coalesce(nullif(display_brand, ''), brand),
  display_model_sku = coalesce(nullif(display_model_sku, ''), model_sku),
  dimensions_text = coalesce(nullif(dimensions_text, ''), dimensions),
  first_seen_at = coalesce(first_seen_at, created_at, now())
where
  normalized_model_sku is null
  or display_brand is null
  or display_model_sku is null
  or dimensions_text is null
  or first_seen_at is null;

create unique index if not exists stored_product_sources_brand_model_sku_unique_idx
  on public.stored_product_sources (normalized_brand, normalized_model_sku);

create table if not exists public.preferred_source_domains (
  id uuid primary key default gen_random_uuid(),
  domain text not null unique,
  source_type text default 'manufacturer',
  notes text,
  success_count integer default 0,
  failure_count integer default 0,
  last_success_at timestamptz,
  last_failure_at timestamptz,
  downranked boolean default false,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists preferred_source_domains_type_idx
  on public.preferred_source_domains (source_type);

-- The backend uses SUPABASE_SERVICE_ROLE_KEY for writes. If you expose these
-- tables to authenticated clients later, add RLS policies before doing so.
