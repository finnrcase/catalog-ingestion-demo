-- Split product-level memory from domain-level success memory.
--
-- Exact product lookup rows store verified product-specific facts.
-- Source success rows store aggregate brand/category/domain performance only.

alter table public.exact_product_lookup_cache
  add column if not exists product_name_hash text not null default '',
  add column if not exists cloudinary_url text not null default '';

alter table public.source_success_registry
  add column if not exists average_confidence numeric(6, 4) not null default 0,
  add column if not exists image_success_rate numeric(6, 4) not null default 0,
  add column if not exists dimension_success_rate numeric(6, 4) not null default 0;

create index if not exists idx_exact_product_lookup_name_hash
  on public.exact_product_lookup_cache (product_name_hash);

create index if not exists idx_source_success_domain_perf
  on public.source_success_registry (normalized_brand, normalized_category, domain, success_count, failure_count);
