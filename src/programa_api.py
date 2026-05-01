"""
Programa HTTP fast-path for item creation and image upload.

Bypasses Playwright UI automation for the product-creation flow:
  1. extract_session(page)         — pull cookies + CSRF from an authenticated browser
  2. extract_section_id(page, ...) — find a section's numeric ID from the live DOM
  3. ProgramaAPIClient              — pure-HTTP calls for create / upload / patch

Use ProgramaAPIClient.create_and_fill_item() as the primary entry point.
Falls back to UI automation when session extraction or any HTTP step fails.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests


BASE_URL = "https://app.programa.design"


@dataclass
class ProgramaSession:
    cookies: dict
    csrf_token: str
    base_url: str = BASE_URL


# ── Session extraction ────────────────────────────────────────────────────────

def extract_session(page) -> ProgramaSession | None:
    """
    Pull auth cookies and CSRF token out of an already-logged-in Playwright page.
    Returns None if extraction fails (caller should fall back to UI automation).
    """
    try:
        cookies = {c["name"]: c["value"] for c in page.context.cookies()}
        print(f"[API] extract_session: {len(cookies)} cookies found")
    except Exception as exc:
        print(f"[API] session_extraction_failed: cookie_read_error — {exc}")
        return None

    if not cookies:
        print("[API] session_extraction_failed: no_cookies — browser may not be logged in")
        return None

    _JS_EXPRS = [
        ("meta_csrf_token",      "() => document.querySelector('meta[name=\"csrf-token\"]')?.content ?? ''"),
        ("authenticity_token",   "() => document.querySelector('[name=\"authenticity_token\"]')?.value ?? ''"),
        ("cookie_csrf_token",    "() => (document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]+)/) || [])[1] ?? ''"),
    ]
    csrf = ""
    for label, js in _JS_EXPRS:
        try:
            val = (page.evaluate(js) or "").strip()
            print(f"[API] csrf_probe {label}: {'found' if val else 'empty'}")
            if val:
                csrf = val
                break
        except Exception as exc:
            print(f"[API] csrf_probe {label}: exception — {exc}")

    if not csrf:
        print("[API] session_extraction_failed: csrf_missing — all 3 CSRF probes returned empty")
        return None

    print(f"[API] Session extracted — {len(cookies)} cookies, CSRF ok (first 8: {csrf[:8]}…)")
    return ProgramaSession(cookies=cookies, csrf_token=csrf)


# ── Section ID extraction ─────────────────────────────────────────────────────

def extract_section_id(page, section_name: str) -> str | None:
    """
    Find the numeric Programa section ID for a visible section heading.

    Tries several DOM patterns in order:
    1. data-section-id / data-id / data-schedule-section-id on section elements
    2. ID in an ancestor of a heading whose text matches
    3. Network-request log interception (looks at XHR responses already loaded)
    """
    norm = section_name.strip().lower()

    print(f"[API] extract_section_id: looking for {section_name!r} (norm={norm!r})")

    # Strategy 1 & 2: DOM attribute walk
    try:
        result = page.evaluate(
            """
            (norm) => {
                const found = [];
                // Strategy 1: elements with known section-ID attributes
                for (const attr of ['data-section-id', 'data-schedule-section-id', 'data-id']) {
                    for (const el of document.querySelectorAll('[' + attr + ']')) {
                        const txt = (el.textContent || '').toLowerCase();
                        if (txt.includes(norm)) {
                            return {id: el.getAttribute(attr), strategy: 'attr:' + attr};
                        }
                    }
                }
                // Strategy 2: walk up from heading
                for (const h of document.querySelectorAll('h1,h2,h3,[class*="section"]')) {
                    if (!(h.textContent || '').toLowerCase().includes(norm)) continue;
                    let el = h;
                    for (let i = 0; i < 6; i++) {
                        if (!el) break;
                        for (const attr of ['data-section-id', 'data-schedule-section-id', 'data-id']) {
                            const v = el.getAttribute(attr);
                            if (v && /^\\d+$/.test(v)) return {id: v, strategy: 'ancestor:' + attr};
                        }
                        el = el.parentElement;
                    }
                }
                // Strategy 3: hidden form inputs
                for (const input of document.querySelectorAll('input[name*="section_id"]')) {
                    const nearby = input.closest('[class*="section"]') || input.parentElement;
                    if (nearby && (nearby.textContent || '').toLowerCase().includes(norm)) {
                        return {id: input.value, strategy: 'input[section_id]'};
                    }
                }
                // Diagnostics: count candidate elements
                const attrs = ['data-section-id', 'data-schedule-section-id', 'data-id'];
                const counts = {};
                for (const a of attrs) counts[a] = document.querySelectorAll('[' + a + ']').length;
                return {id: null, counts: counts,
                    headings: Array.from(document.querySelectorAll('h1,h2,h3')).map(h => h.textContent.trim().slice(0,60)).slice(0,10)};
            }
            """,
            norm,
        )
        if result and result.get("id"):
            sid = str(result["id"])
            print(f"[API] section_id_found via {result.get('strategy','?')}: {sid!r} for {section_name!r}")
            return sid
        # Log why it wasn't found
        if result:
            print(
                f"[API] section_id_not_found: DOM attrs counts={result.get('counts')} "
                f"headings_visible={result.get('headings')}"
            )
    except Exception as exc:
        print(f"[API] section_id_not_found: dom_walk_exception — {exc}")

    # Strategy 4: href-based numeric IDs
    try:
        result = page.evaluate(
            """
            (norm) => {
                for (const a of document.querySelectorAll('a[href*="section"]')) {
                    const m = a.href.match(/sections?\\/?(\\d+)/);
                    if (m) {
                        const container = a.closest('[class*="section"]') || a.parentElement;
                        if (container && (container.textContent || '').toLowerCase().includes(norm)) {
                            return {id: m[1], href: a.href};
                        }
                    }
                }
                const allHrefs = Array.from(document.querySelectorAll('a[href*="section"]')).map(a => a.href).slice(0,5);
                return {id: null, sample_hrefs: allHrefs};
            }
            """,
            norm,
        )
        if result and result.get("id"):
            sid = str(result["id"])
            print(f"[API] section_id_found via href: {sid!r} (from {result.get('href')})")
            return sid
        if result:
            print(f"[API] section_id_not_found: href_strategy — sample hrefs: {result.get('sample_hrefs')}")
    except Exception as exc:
        print(f"[API] section_id_not_found: href_exception — {exc}")

    print(f"[API] section_id_not_found: exhausted all strategies for {section_name!r}")
    return None


# ── MD5 checksum helper ────────────────────────────────────────────────────────

def _md5_base64(path: Path) -> str:
    """Base64-encoded MD5 digest — Rails Active Storage checksum format."""
    md5 = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            md5.update(chunk)
    return base64.b64encode(md5.digest()).decode()


# ── HTTP client ───────────────────────────────────────────────────────────────

class ProgramaAPIClient:
    """
    Pure-HTTP Programa client.  All methods return simple success values
    (str | bool | dict) — never raise.
    """

    def __init__(self, session: ProgramaSession) -> None:
        self.session = session
        self.base_url = session.base_url.rstrip("/")
        self._http = requests.Session()
        self._http.cookies.update(session.cookies)
        self._http.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })

    # ── 1. Create blank item ──────────────────────────────────────────────────

    def create_item(self, section_id: str) -> str | None:
        """
        POST /schedules2/schedule_section_items
        Returns the new item's numeric ID, or None on failure.
        """
        url = f"{self.base_url}/schedules2/schedule_section_items"
        data = {
            "authenticity_token": self.session.csrf_token,
            "schedule_section_item[schedule_section_id]": section_id,
        }
        print(f"[API] create_item: POST {url} section_id={section_id!r}")
        try:
            resp = self._http.post(url, data=data, timeout=20, allow_redirects=True)
            print(f"[API] create_item → {resp.status_code} (final_url={resp.url})")
            if resp.status_code in (200, 201):
                try:
                    body = resp.json()
                    item_id = str(body.get("id") or body.get("item_id") or "").strip()
                    if item_id:
                        print(f"[API] create_item: item_id={item_id!r} (from JSON)")
                        return item_id
                    print(f"[API] create_item_failed_no_item_id: JSON keys={list(body.keys())[:10]}")
                except (ValueError, KeyError) as je:
                    print(f"[API] create_item: JSON parse failed ({je}), trying URL pattern")
                # Extract from redirect / final URL
                m = re.search(r"/schedule_section_items/(\d+)", resp.url)
                if m:
                    print(f"[API] create_item: item_id={m.group(1)!r} (from final URL)")
                    return m.group(1)
                print(f"[API] create_item_failed_no_item_id: body_preview={resp.text[:300]!r}")
            elif resp.status_code in (302, 303):
                location = resp.headers.get("Location", "")
                m = re.search(r"/schedule_section_items/(\d+)", location)
                if m:
                    print(f"[API] create_item: item_id={m.group(1)!r} (from Location header)")
                    return m.group(1)
                print(f"[API] create_item_failed_no_item_id: Location={location!r}")
            elif resp.status_code in (401, 403):
                print(f"[API] create_item_failed_status_{resp.status_code}: auth/CSRF rejected — body={resp.text[:200]!r}")
            elif resp.status_code == 422:
                print(f"[API] create_item_failed_status_422: validation error — body={resp.text[:300]!r}")
            else:
                print(f"[API] create_item_failed_status_{resp.status_code}: body={resp.text[:300]!r}")
        except Exception as exc:
            print(f"[API] create_item_failed_exception: {exc}")
        return None

    # ── 2+3. Active Storage direct upload ────────────────────────────────────

    def direct_upload_image(self, image_path: Path) -> str | None:
        """
        Two-step Rails Active Storage direct upload:
          a) POST /rails/active_storage/direct_uploads  → get signed_id + S3 URL
          b) PUT  <S3 URL>                              → upload raw bytes

        Returns the blob signed_id to be used in update_item(), or None on failure.
        """
        path = Path(image_path)
        if not path.exists():
            print(f"[API] Image not found: {path}")
            return None

        size = path.stat().st_size
        content_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        checksum = _md5_base64(path)

        print(f"[API] direct_upload_image: {path.name} ({size} bytes, {content_type})")

        # Step a: create blob record
        blob_url = f"{self.base_url}/rails/active_storage/direct_uploads"
        payload = {
            "blob": {
                "filename": path.name,
                "content_type": content_type,
                "byte_size": size,
                "checksum": checksum,
            }
        }
        try:
            resp = self._http.post(
                blob_url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=20,
            )
            print(f"[API] direct_upload_blob → {resp.status_code}")
            if resp.status_code in (401, 403):
                print(f"[API] direct_upload_blob_failed_status_{resp.status_code}: auth/CSRF rejected — {resp.text[:200]!r}")
                return None
            if resp.status_code == 422:
                print(f"[API] direct_upload_blob_failed_status_422: checksum/size validation — {resp.text[:300]!r}")
                return None
            if resp.status_code not in (200, 201):
                print(f"[API] direct_upload_blob_failed_status_{resp.status_code}: {resp.text[:300]!r}")
                return None

            blob = resp.json()
            signed_id = blob.get("signed_id", "")
            upload_info = blob.get("direct_upload", {})
            upload_url = upload_info.get("url", "")
            upload_headers = upload_info.get("headers", {})

            if not signed_id:
                print(f"[API] direct_upload_blob_failed_no_signed_id: keys={list(blob.keys())}")
                return None
            if not upload_url:
                print(f"[API] direct_upload_blob_failed_no_upload_url: direct_upload={upload_info}")
                return None
            print(f"[API] direct_upload_blob: signed_id=…{signed_id[-12:]}, upload_url host={upload_url.split('/')[2] if '/' in upload_url else upload_url[:40]}")

        except Exception as exc:
            print(f"[API] direct_upload_blob_failed_exception: {exc}")
            return None

        # Step b: PUT raw bytes to S3
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            s3_resp = requests.put(
                upload_url,
                data=raw,
                headers=upload_headers,
                timeout=120,
            )
            print(f"[API] s3_upload → {s3_resp.status_code}")
            if s3_resp.status_code not in (200, 204):
                print(f"[API] s3_upload_failed_status_{s3_resp.status_code}: {s3_resp.text[:200]!r}")
                return None

            print(f"[API] image_uploaded: {path.name} ({size} bytes) signed_id=…{signed_id[-12:]}")
            return signed_id

        except Exception as exc:
            print(f"[API] s3_upload_failed_exception: {exc}")
            return None

    # ── 4. Patch item ─────────────────────────────────────────────────────────

    def update_item(
        self,
        item_id: str,
        fields: dict,
        signed_id: str | None = None,
    ) -> bool:
        """
        POST /schedules2/schedule_section_items/<id>  (_method=patch)

        fields keys (any subset):
            product_name, brand, width, height, depth, length,
            colour / color, finish, quantity, lead_time, material,
            sku / model, product_url, description, notes, price
        """
        url = f"{self.base_url}/schedules2/schedule_section_items/{item_id}"

        _FIELD_MAP = {
            "product_name":  "schedule_section_item[product_name]",
            "brand":         "schedule_section_item[brand]",
            "width":         "schedule_section_item[width]",
            "height":        "schedule_section_item[height]",
            "depth":         "schedule_section_item[depth]",
            "length":        "schedule_section_item[length]",
            "colour":        "schedule_section_item[colour]",
            "color":         "schedule_section_item[colour]",
            "finish":        "schedule_section_item[finish]",
            "quantity":      "schedule_section_item[quantity]",
            "lead_time":     "schedule_section_item[lead_time]",
            "material":      "schedule_section_item[material]",
            "sku":           "schedule_section_item[sku]",
            "model":         "schedule_section_item[sku]",
            "product_url":   "schedule_section_item[product_url]",
            "description":   "schedule_section_item[description]",
            "notes":         "schedule_section_item[notes]",
            "price":         "schedule_section_item[price]",
            "supplier":      "schedule_section_item[supplier]",
        }

        data: dict = {
            "_method": "patch",
            "authenticity_token": self.session.csrf_token,
            "item-id": f"supplier_search_{item_id}",
        }

        for key, api_key in _FIELD_MAP.items():
            value = str(fields.get(key, "") or "").strip()
            if value:
                data[api_key] = value

        if signed_id:
            data["schedule_section_item[images][]"] = signed_id

        filled_fields = [k for k in _FIELD_MAP if fields.get(k)]
        print(f"[API] update_item {item_id}: fields={filled_fields} image={'yes' if signed_id else 'no'}")
        try:
            resp = self._http.post(url, data=data, timeout=25, allow_redirects=True)
            print(f"[API] update_item {item_id} → {resp.status_code}")
            if resp.status_code in (200, 201, 204, 302, 303):
                print(f"[API] update_item {item_id}: success")
                return True
            elif resp.status_code in (401, 403):
                print(f"[API] update_item_failed_status_{resp.status_code}: auth/CSRF rejected — {resp.text[:200]!r}")
            elif resp.status_code == 404:
                print(f"[API] update_item_failed_status_404: item {item_id} not found on server")
            elif resp.status_code == 422:
                print(f"[API] update_item_failed_status_422: validation error — {resp.text[:300]!r}")
            else:
                print(f"[API] update_item_failed_status_{resp.status_code}: {resp.text[:300]!r}")
        except Exception as exc:
            print(f"[API] update_item_failed_exception: {exc}")
        return False

    # ── Combined fast-path ────────────────────────────────────────────────────

    def create_and_fill_item(
        self,
        section_id: str,
        fields: dict,
        image_path: str | None = None,
    ) -> dict:
        """
        Full HTTP fast-path:
          1. Create blank item  → item_id
          2. Direct-upload image → signed_id  (skipped when image_path absent)
          3. Patch fields + image

        Returns:
            {
              "ok": bool,
              "item_id": str | None,
              "image_status": "uploaded" | "failed" | "skipped",
              "update_ok": bool,
              "error": str   (only on failure)
            }
        """
        result: dict = {
            "ok": False,
            "item_id": None,
            "image_status": "skipped",
            "update_ok": False,
        }

        # 1. Create blank item
        print(f"[API] Creating blank item in section {section_id}")
        item_id = self.create_item(section_id)
        if not item_id:
            result["error"] = "create_item failed"
            print("[API] Fast-path failed at create_item step")
            return result
        result["item_id"] = item_id
        print(f"[API] Blank item created: {item_id}")

        # 2. Upload image
        signed_id: str | None = None
        if image_path and Path(image_path).exists():
            print(f"[API] Uploading image: {image_path}")
            signed_id = self.direct_upload_image(Path(image_path))
            result["image_status"] = "uploaded" if signed_id else "failed"
            if not signed_id:
                print("[API] Image upload failed — item will be created without image")
        else:
            print("[API] No image path provided — skipping image upload")

        # 3. Patch fields
        print(f"[API] Patching item {item_id} with product fields")
        update_ok = self.update_item(item_id, fields, signed_id=signed_id)
        result["update_ok"] = update_ok

        # ok = True only when fields were patched AND the image either uploaded
        # successfully or was never requested — never "success" with a failed upload
        image_ok = result["image_status"] in ("uploaded", "skipped")
        result["ok"] = update_ok and image_ok

        if result["ok"]:
            print(
                f"[API] Fast-path complete: item {item_id} created and filled "
                f"(image: {result['image_status']})"
            )
        else:
            if not update_ok:
                result["error"] = "update_item failed"
                print(f"[API] Fast-path failed at update_item step for item {item_id}")
            else:
                result["error"] = "image_upload_failed"
                print(f"[API] Fast-path: image upload failed for item {item_id} — not marking success")

        return result

    def create_image_only_item(self, section_id: str, image_path: str) -> dict:
        """
        Fast photo-only path:
          1. Create blank item in the target section.
          2. Direct-upload image through Active Storage.
          3. Patch only schedule_section_item[images][] with the signed blob ID.

        No product text/data fields are sent.
        """
        result: dict = {
            "ok": False,
            "item_id": None,
            "image_status": "failed",
            "update_ok": False,
            "signed_id": None,
        }

        print(f"[API] Photo-only: creating blank item in section {section_id}")
        item_id = self.create_item(section_id)
        if not item_id:
            result["error"] = "create_item failed"
            return result
        result["item_id"] = item_id

        signed_id = self.direct_upload_image(Path(image_path))
        if not signed_id:
            result["error"] = "direct_upload_image failed"
            return result
        result["signed_id"] = signed_id
        result["image_status"] = "uploaded"

        update_ok = self.update_item(item_id, {}, signed_id=signed_id)
        result["update_ok"] = update_ok
        result["ok"] = bool(update_ok)
        if not update_ok:
            result["error"] = "update_item image patch failed"
        return result


# ── Row dict → API fields mapping ─────────────────────────────────────────────

def row_to_api_fields(row: dict) -> dict:
    """
    Convert an intake row dict to the flat field dict expected by
    ProgramaAPIClient.update_item() / create_and_fill_item().
    """
    def v(*keys: str) -> str:
        for k in keys:
            val = str(row.get(k, "") or "").strip()
            if val:
                return val
        return ""

    return {
        "product_name": v("Product Name", "product_name", "name"),
        "brand":        v("Brand", "brand"),
        "sku":          v("Model/SKU", "sku", "model", "Serial / Model Number"),
        "description":  v("Description", "description"),
        "width":        v("width_in", "Width", "W"),
        "height":       v("height_in", "Height", "H"),
        "depth":        v("depth_in", "Depth", "D"),
        "length":       v("length_in", "Length", "L"),
        "colour":       v("Color", "Colour", "color", "colour", "Finish / Color"),
        "finish":       v("Finish", "finish", "Finish / Color"),
        "material":     v("material", "Material"),
        "quantity":     v("Quantity", "quantity") or "0",
        "lead_time":    v("lead_time", "Lead Time"),
        "product_url":  v("product_url", "Product URL"),
        "price":        v("Price", "price"),
        "notes":        v("Notes", "notes"),
        "supplier":     v("Supplier", "supplier"),
    }
