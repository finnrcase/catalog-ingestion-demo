# Enrichment Cache & Search Budget Design

**Date:** 2026-05-03  
**Status:** Approved  
**Scope:** Backend only — no frontend changes in this phase

---

## Problem

Every call to `enrich_row()` hits the Brave Search API fresh. The same brand domain is rediscovered on every restart (session-only `_discovered_domains` is lost). The same product is searched repeatedly across runs. Within a single batch, identical Brave queries and URL fetches are duplicated across rows. There is no budget cap — a 24-row file may burn 100+ Brave API calls.

---

## Goal

- Stop repeated Brave calls for the same brand domain or product
- Persist manufacturer domain lookups and product enrichment results across restarts
- Deduplicate identical queries and URL fetches within one batch run
- Enforce a per-product Brave search budget keyed to enrichment mode
- Expose enrichment mode as an optional API parameter (default: `"standard"`)
- Surface cache hit/miss diagnostics in the existing API response

---

## Approach: New `src/enrichment_cache.py` Module (Option A)

One new module owns all caching and budget logic. Existing modules are minimally changed — no restructuring of the dimension pipeline.

---

## 1. New Module: `src/enrichment_cache.py`

### 1.1 `normalize_key(brand, model) -> str`

```python
normalize_key("Sub-Zero", "BI-36U/S") -> "subzero_bi36us"
```

Lowercased, spaces/dashes/special characters stripped, joined with `_`. Used as the product cache key.

### 1.2 `ManufacturerDomainCache`

Wraps `data/manufacturer_domain_cache.json`. Lazy-loads on first access; creates file if missing.

```python
class ManufacturerDomainCache:
    def get(self, brand_key: str) -> dict | None: ...
    def set(self, brand_key: str, domain: str, source: str) -> None: ...
    def _load(self) -> None: ...   # idempotent, called on first access
    def _save(self) -> None: ...   # atomic write via temp file
```

Cache file schema:
```json
{
  "scotsman": {
    "domain": "scotsman-ice.com",
    "source": "hardcoded",
    "last_verified": "2026-05-03"
  },
  "acme": {
    "domain": "acme.com",
    "source": "discovered",
    "last_verified": "2026-05-03"
  }
}
```

- `source` is `"hardcoded"` for entries seeded from `BRAND_DOMAIN_TABLE`; `"discovered"` for entries found via live Brave search.
- Hardcoded entries are never overwritten by discovery.
- On startup, all entries from `BRAND_DOMAIN_TABLE` are available in the hardcoded table (not written to disk unless `get()` is called and they are absent from the JSON — the hardcoded table remains the source of truth for known brands).

### 1.3 `ProductEnrichmentCache`

Wraps `data/product_enrichment_cache.json`. Lazy-loads on first access; creates file if missing.

```python
class ProductEnrichmentCache:
    def get(self, key: str) -> dict | None: ...
    def update(self, key: str, fields: dict) -> None: ...  # merge, never replace whole entry
    def _load(self) -> None: ...
    def _save(self) -> None: ...
```

Cache file schema (one entry per product):
```json
{
  "scotsman_hv48ss": {
    "product_url": "https://scotsman-ice.com/...",
    "image_url": null,
    "dimensions": "47.8\"W x 33.5\"H x 24.1\"D",
    "width_in": "47.8",
    "height_in": "33.5",
    "depth_in": "24.1",
    "length_in": null,
    "material": "Stainless Steel",
    "finish": "Stainless Steel",
    "source_url": "https://...",
    "dimension_source_url": "https://...",
    "general_confidence": "high",
    "dimension_confidence": "high",
    "timestamp": "2026-05-03T12:00:00",
    "null_fields": {
      "image_url": {
        "last_attempted": "2026-05-03T12:00:00",
        "failure_reason": "not found on manufacturer page"
      }
    }
  }
}
```

**Key distinctions:**
- `null` value = searched but not found → skip on next run (unless `force_refresh=True`)
- Missing key = never searched → eligible for search
- `null_fields` stores per-field metadata for null results (timestamp + failure reason) — used for diagnostics and future revalidation logic

**`update()` rules:**
- Only write fields that were actually returned by a live search (found or explicitly not-found)
- Do not write blank row defaults into the cache
- Merge new fields into existing entry; do not replace the whole entry
- Atomic save via temp file + rename

### 1.4 `SessionCache`

In-memory, created once per `enrich_dataframe()` call. Never persisted to disk.

```python
@dataclass
class SessionCache:
    queries: dict[str, list]    # raw query string → list[SearchResult]
    urls: dict[str, str]        # url → page text or raw html
    force_refresh: bool = False
```

- `force_refresh=True`: bypasses cached `null` values in `ProductEnrichmentCache` so missing fields are re-searched. Does **not** ignore non-null high/medium cached values.
- Deduplicated at both the Brave query level and the URL fetch level within one batch run.

### 1.5 `SearchBudget`

Per-product, per-row. Created fresh in `enrich_row()`.

```python
@dataclass
class SearchBudget:
    max_searches: int
    max_urls: int
    searches_used: int = 0
    urls_used: int = 0

    def can_search(self) -> bool: ...
    def can_fetch(self) -> bool: ...
    def consume_search(self) -> None: ...
    def consume_fetch(self) -> None: ...
```

Counts only **actual Brave API calls** — session cache hits do not count against budget.

### 1.6 Mode → Budget Limits

| Mode | Max Brave searches | Max URLs | Retailer fallback | General Brave fallback |
|---|---|---|---|---|
| `fast` | 1 | 3 | No | No |
| `standard` | 4 | 5 | No | 1 general fallback |
| `deep` | 8 | 10 | Yes | Yes |

Env overrides:
```
BRAVE_MAX_SEARCHES_PER_PRODUCT=4       # overrides standard default
ENRICHMENT_MAX_URLS_PER_PRODUCT=5      # overrides standard default
ENRICHMENT_CACHE_ENABLED=true          # set false to disable both persistent caches (useful for testing)
```

`enrichment_mode` validates to `fast | standard | deep`; any unrecognized value falls back to `standard`.

**Fast mode behavior:** If the manufacturer domain is known (hardcoded table or persistent cache), skip the general enrichment Brave call entirely and go straight to one targeted `site:{domain} "{model}"` query. If the domain is unknown, run one general query only.

---

## 2. Changes to Existing Modules

### 2.1 `src/brave_search.py`

- `search_product_candidates()` gains optional `session_cache: SessionCache | None = None`
- Before calling Brave API: `session_cache.queries.get(query)` → return cached result if present (no budget consumed)
- After calling Brave API: store result in `session_cache.queries[query]`
- No other changes

### 2.2 `src/dimension_enrichment.py`

- Remove module-level `_discovered_domains: dict[str, str] = {}`
- Import `ManufacturerDomainCache` from `enrichment_cache`; instantiate as a module-level singleton (lazy-loads on first `get()`)
- `_get_manufacturer_domain()`:
  - Check `BRAND_DOMAIN_TABLE` first (unchanged)
  - Then call `mfr_cache.get(key)` for persistent discovered cache
  - On live discovery: call `mfr_cache.set(key, domain, source="discovered")`
- `_brave_search_urls()`: add `session_cache: SessionCache | None = None`, `budget: SearchBudget | None = None`; check session cache, check budget before calling Brave
- `find_dimensions()`: add `session_cache: SessionCache | None = None`, `budget: SearchBudget | None = None`; thread both through to `_brave_search_urls()`
- No changes to parsing logic, query generation, or confidence assignment

### 2.3 `src/product_enrichment.py`

`enrich_row()` signature change:
```python
def enrich_row(
    row: dict,
    enrichment_mode: str = "standard",
    session_cache: SessionCache | None = None,
) -> tuple[dict, str | None, DimensionResult | None]:
```

**New flow at entry:**
1. Compute `cache_key = normalize_key(brand, model)`
2. Load `ProductEnrichmentCache` entry → `cached`
3. Fill row fields where `cached[field]` is non-null AND confidence is high/medium (never overwrite existing row values)
4. Determine missing essentials: `dimensions` complete + `product_url` present
5. If no essentials missing → log `[CACHE HIT: full]` → return early
6. Log `[CACHE HIT: partial]` or `[CACHE MISS]` as appropriate
7. Create `SearchBudget` from mode limits
8. Run general enrichment Brave search only for missing fields (respecting budget, using session cache)
9. Pass `session_cache` and `budget` into `find_dimensions()`
10. After all enrichment: call `product_cache.update(cache_key, fields_actually_found_or_searched)`

`enrich_dataframe()` signature change:
```python
def enrich_dataframe(
    df: pd.DataFrame,
    enrichment_mode: str = "standard",
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, list[str], list[dict]]:
```

Creates `SessionCache(force_refresh=force_refresh)` once; passes to every `enrich_row()` call.

### 2.4 `backend/main.py`

`RowsPayload` gains:
```python
enrichment_mode: str = "standard"
force_refresh: bool = False
```

`enrich_intake()` passes both to `enrich_dataframe()`.

---

## 3. Partial Cache Hit Logic (Detail)

```
essential_fields = ["dimensions", "product_url"]

cache_entry = product_cache.get(key)  # None if never searched

if cache_entry:
    for field in ALL_CACHEABLE_FIELDS:
        cached_val = cache_entry.get(field)
        if cached_val is None:
            # null = searched and not found
            if not session_cache.force_refresh:
                log("[CACHED NULL SKIPPED] field={field}")
                continue   # skip re-searching this field
            else:
                log("[CACHED NULL BYPASSED by force_refresh] field={field}")
        elif cached_val and confidence_ok(cache_entry, field):
            fill_row_field(row, field, cached_val)
            cache_fields_filled.append(field)

    missing = [f for f in essential_fields if not row_has_usable_value(row, f)]
    if not missing:
        log("[CACHE HIT: full] key={key}")
        return early
    log("[CACHE HIT: partial] key={key} still_missing={missing}")
    fields_searched.extend(missing)
else:
    log("[CACHE MISS] key={key}")
    fields_searched.extend(essential_fields)

# ... run search with budget ...
```

---

## 4. Diagnostics

### 4.1 New fields on `DimensionResult`

```python
domain_source: str = "none"       # "hardcoded" | "persistent_cache" | "discovered_live" | "none"
product_cache_hit: str = "none"   # "full" | "partial" | "miss"
brave_searches_used: int = 0      # actual API calls only; cache hits do not count
```

### 4.2 `dimension_diagnostics` dict (per row in API response)

New keys merged into the existing dict:
```json
{
  "domain_source": "hardcoded",
  "product_cache_hit": "partial",
  "brave_searches_used": 2,
  "cache_fields_filled": ["dimensions", "width_in", "height_in", "depth_in"],
  "fields_searched": ["product_url"],
  "enrichment_mode": "standard"
}
```

### 4.3 Log event labels (for grep/filtering)

All enrichment log lines use consistent bracketed prefixes:

| Label | Meaning |
|---|---|
| `[CACHE HIT: full]` | All essentials present in cache; no Brave calls made |
| `[CACHE HIT: partial]` | Some essentials from cache; live search for remainder |
| `[CACHE MISS]` | No cache entry; full search |
| `[CACHED NULL SKIPPED]` | Field was searched before and not found; skipped |
| `[CACHED NULL BYPASSED]` | force_refresh=True; re-searching a previously null field |
| `[LIVE SEARCH]` | Actual Brave API call made |
| `[SESSION CACHE HIT]` | Query or URL served from session dedup cache |
| `[BUDGET EXHAUSTED]` | Budget limit reached; remaining searches skipped |

---

## 5. Cache File Safety

- Both cache files are created (empty `{}`) if they do not exist
- All saves use atomic write: write to `.tmp` file, then `os.replace()` to final path
- Load errors (malformed JSON) log a warning and treat cache as empty — never crash enrichment
- Cache directory (`data/`) is assumed to exist (already in use by other features)

---

## 6. Out of Scope (This Phase)

- Frontend Enrichment Mode dropdown
- Full cache entry replacement (manual "full refresh" that ignores all cached values, including non-null)
- Automatic TTL-based expiration
- Image URL lookup (image_url field is stored in cache schema but not actively searched in this phase)
- `ENRICHMENT_MAX_SECONDS_PER_PRODUCT` timeout (env var reserved but not implemented)

---

## 7. Acceptance Criteria

1. Running the same 24-row file twice: second run uses near-zero Brave API calls for products already cached
2. `data/manufacturer_domain_cache.json` and `data/product_enrichment_cache.json` are created on first run and populated with results
3. `DimensionResult` includes `domain_source`, `product_cache_hit`, `brave_searches_used`
4. `enrich_intake` API accepts `enrichment_mode` and `force_refresh` fields
5. Session deduplication: identical Brave query within one batch run fires only once
6. Budget: a product in standard mode never triggers more than 4 actual Brave API calls
7. Existing tests pass without modification (new params are optional with defaults)

---

## 8. Files Changed

| File | Type | Change size |
|---|---|---|
| `src/enrichment_cache.py` | New | ~150 lines |
| `src/dimension_enrichment.py` | Modified | ~25 lines |
| `src/product_enrichment.py` | Modified | ~70 lines |
| `src/brave_search.py` | Modified | ~15 lines |
| `backend/main.py` | Modified | ~15 lines |
| `data/manufacturer_domain_cache.json` | New (runtime) | Created on first run |
| `data/product_enrichment_cache.json` | New (runtime) | Created on first run |
