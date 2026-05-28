-- Durable enrichment memory for SCH DesignOps Intake.
-- These tables are written/read by the FastAPI backend with the Supabase
-- service role key only. Do not expose service role credentials to the client.

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.product_enrichment_cache (
  cache_key text primary key,
  normalized_brand text not null default '',
  normalized_model text not null default '',
  confidence text not null default '',
  source_url text not null default '',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.exact_product_lookup_cache (
  cache_key text primary key,
  normalized_brand text not null default '',
  normalized_sku text not null default '',
  confidence text not null default '',
  source_type text not null default '',
  selected_product_url text not null default '',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.manufacturer_domain_cache (
  brand text primary key,
  official_domain text not null default '',
  source text not null default '',
  confidence text not null default '',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.source_success_registry (
  cache_key text primary key,
  normalized_brand text not null default '',
  normalized_sku text not null default '',
  normalized_category text not null default '',
  domain text not null default '',
  success_count integer not null default 0,
  failure_count integer not null default 0,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.preferred_product_websites (
  id text primary key,
  keyword text not null default '',
  domain text not null default '',
  url text not null default '',
  success_count integer not null default 0,
  failure_count integer not null default 0,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.enrichment_cost_history (
  id text primary key,
  upload_id text not null default '',
  project_name text not null default '',
  file_name text not null default '',
  bravi_cost_usd numeric(12, 6) not null default 0,
  total_enrichment_cost_usd numeric(12, 6) not null default 0,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.image_cache (
  cache_key text primary key,
  normalized_brand text not null default '',
  normalized_sku text not null default '',
  image_url text not null default '',
  source_url text not null default '',
  confidence text not null default '',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_product_enrichment_cache_brand on public.product_enrichment_cache (normalized_brand);
create index if not exists idx_exact_product_lookup_brand_sku on public.exact_product_lookup_cache (normalized_brand, normalized_sku);
create index if not exists idx_manufacturer_domain_cache_domain on public.manufacturer_domain_cache (official_domain);
create index if not exists idx_source_success_brand_category on public.source_success_registry (normalized_brand, normalized_category);
create index if not exists idx_source_success_domain on public.source_success_registry (domain);
create index if not exists idx_preferred_product_websites_domain on public.preferred_product_websites (domain);
create index if not exists idx_enrichment_cost_history_upload on public.enrichment_cost_history (upload_id);
create index if not exists idx_image_cache_brand_sku on public.image_cache (normalized_brand, normalized_sku);

drop trigger if exists set_product_enrichment_cache_updated_at on public.product_enrichment_cache;
create trigger set_product_enrichment_cache_updated_at
before update on public.product_enrichment_cache
for each row execute function public.set_updated_at();

drop trigger if exists set_exact_product_lookup_cache_updated_at on public.exact_product_lookup_cache;
create trigger set_exact_product_lookup_cache_updated_at
before update on public.exact_product_lookup_cache
for each row execute function public.set_updated_at();

drop trigger if exists set_manufacturer_domain_cache_updated_at on public.manufacturer_domain_cache;
create trigger set_manufacturer_domain_cache_updated_at
before update on public.manufacturer_domain_cache
for each row execute function public.set_updated_at();

drop trigger if exists set_source_success_registry_updated_at on public.source_success_registry;
create trigger set_source_success_registry_updated_at
before update on public.source_success_registry
for each row execute function public.set_updated_at();

drop trigger if exists set_preferred_product_websites_updated_at on public.preferred_product_websites;
create trigger set_preferred_product_websites_updated_at
before update on public.preferred_product_websites
for each row execute function public.set_updated_at();

drop trigger if exists set_enrichment_cost_history_updated_at on public.enrichment_cost_history;
create trigger set_enrichment_cost_history_updated_at
before update on public.enrichment_cost_history
for each row execute function public.set_updated_at();

drop trigger if exists set_image_cache_updated_at on public.image_cache;
create trigger set_image_cache_updated_at
before update on public.image_cache
for each row execute function public.set_updated_at();

alter table public.product_enrichment_cache enable row level security;
alter table public.exact_product_lookup_cache enable row level security;
alter table public.manufacturer_domain_cache enable row level security;
alter table public.source_success_registry enable row level security;
alter table public.preferred_product_websites enable row level security;
alter table public.enrichment_cost_history enable row level security;
alter table public.image_cache enable row level security;
