from pathlib import Path


def _isolate_source_memory(monkeypatch, tmp_path):
    import src.source_memory as sm

    monkeypatch.setattr(sm, "_SOURCE_MEMORY_ENABLED", True)
    monkeypatch.setattr(sm, "_supabase_configured", lambda: False)
    monkeypatch.setattr(sm, "_PRODUCT_SOURCE_PATH", Path(tmp_path / "stored_product_sources.json"))
    monkeypatch.setattr(sm, "_PREFERRED_DOMAIN_PATH", Path(tmp_path / "preferred_source_domains.json"))
    return sm


def test_product_source_saves_reloads_and_applies_to_row(monkeypatch, tmp_path):
    sm = _isolate_source_memory(monkeypatch, tmp_path)

    saved = sm.upsert_product_source(
        {
            "brand": "Wolf",
            "model_sku": "WWD30",
            "product_name": "Warming Drawer",
            "product_page_url": "https://www.subzero-wolf.com/wolf/warming-drawers/wwd30",
            "manufacturer_url": "https://www.subzero-wolf.com/wolf",
            "dimension_source_url": "https://www.subzero-wolf.com/specs/wwd30.pdf",
            "image_url": "https://res.cloudinary.com/demo/image/upload/wolf_wwd30.jpg",
            "dimensions_text": '30"W x 10"H x 23"D',
            "width_in": "30",
            "height_in": "10",
            "depth_in": "23",
            "source_type": "manufacturer",
            "confidence_score": 92,
            "dimension_confidence": "high",
            "image_confidence": "medium",
            "success_count": 1,
        }
    )

    assert saved["normalized_brand"] == "wolf"
    assert saved["normalized_model_sku"] == "wwd30"
    assert saved["normalized_model"] == "wwd30"
    assert saved["display_brand"] == "Wolf"
    assert saved["display_model_sku"] == "WWD30"
    assert saved["dimensions_text"] == '30"W x 10"H x 23"D'

    source = sm.lookup_product_source("Wolf", "WWD-30")
    assert source["product_page_url"].endswith("/wwd30")

    row, filled = sm.apply_product_source_to_row({"Brand": "Wolf", "Model/SKU": "WWD30"}, source)
    assert row["Product URL"].endswith("/wwd30")
    assert row["Manufacturer URL"] == "https://www.subzero-wolf.com/wolf"
    assert row["Image URL"].startswith("https://res.cloudinary.com")
    assert row["Dimensions"] == '30"W x 10"H x 23"D'
    assert row["Dimension Confidence"] == "high"
    assert row["image_confidence"] == "medium"
    assert {"Product URL", "Image URL", "Dimensions"}.issubset(set(filled))


def test_product_source_strips_invalid_urls_before_lookup(monkeypatch, tmp_path):
    sm = _isolate_source_memory(monkeypatch, tmp_path)

    saved = sm.upsert_product_source(
        {
            "brand": "Fisher & Paykel",
            "model_sku": "RB36S25MKIWN",
            "product_page_url": "https://[bad",
            "dimension_source_url": "https://www.fisherpaykel.com/us/specs/rb36s25mkiwn.pdf",
            "dimensions_text": '36"W x 84"H x 24"D',
            "confidence_score": 90,
            "success_count": 1,
        }
    )

    assert saved["product_page_url"] == ""
    source = sm.lookup_product_source("Fisher & Paykel", "RB36S25MKIWN")
    assert source["product_page_url"] == ""
    assert source["dimension_source_url"].endswith(".pdf")
    row, filled = sm.apply_product_source_to_row({"Brand": "Fisher & Paykel", "Model/SKU": "RB36S25MKIWN"}, source)
    assert "Product URL" not in filled
    assert row["Dimension Source URL"].endswith(".pdf")


def test_product_source_success_count_accumulates(monkeypatch, tmp_path):
    sm = _isolate_source_memory(monkeypatch, tmp_path)

    sm.upsert_product_source({"brand": "Miele", "model_sku": "G7986SCVIK20", "product_page_url": "https://mieleusa.com/a", "confidence_score": 90, "success_count": 1})
    saved = sm.upsert_product_source({"brand": "Miele", "model_sku": "G7986SCVIK20", "image_url": "https://res.cloudinary.com/demo/miele.jpg", "confidence_score": 90, "success_count": 1})

    assert saved["success_count"] == 2
    assert saved["product_page_url"] == "https://mieleusa.com/a"
    assert saved["image_url"].endswith("miele.jpg")


def test_product_source_does_not_overwrite_high_manufacturer_with_lower_retailer(monkeypatch, tmp_path):
    sm = _isolate_source_memory(monkeypatch, tmp_path)

    sm.upsert_product_source({
        "brand": "Bosch",
        "model_sku": "SPV68C73UC",
        "product_page_url": "https://www.bosch-home.com/us/products/spv68c73uc",
        "dimensions_text": '18"W x 32"H x 22"D',
        "source_type": "manufacturer",
        "confidence_score": 92,
        "success_count": 1,
    })
    saved = sm.upsert_product_source({
        "brand": "Bosch",
        "model_sku": "SPV68C73UC",
        "product_page_url": "https://retailer.example.com/bosch-spv68c73uc",
        "dimensions_text": '99"W x 99"H x 99"D',
        "image_url": "https://retailer.example.com/bosch.jpg",
        "source_type": "retailer",
        "confidence_score": 65,
        "success_count": 1,
    })

    assert saved["success_count"] == 2
    assert saved["product_page_url"] == "https://www.bosch-home.com/us/products/spv68c73uc"
    assert saved["dimensions_text"] == '18"W x 32"H x 22"D'
    assert saved["image_url"] == "https://retailer.example.com/bosch.jpg"


def test_preferred_domain_crud(monkeypatch, tmp_path):
    sm = _isolate_source_memory(monkeypatch, tmp_path)

    saved = sm.upsert_preferred_domain({"domain": "https://www.subzero-wolf.com/products", "source_type": "manufacturer", "success_count": 1})
    assert saved["domain"] == "subzero-wolf.com"

    domains = sm.list_preferred_domains(query="subzero")
    assert len(domains) == 1
    assert domains[0]["source_type"] == "manufacturer"

    updated = sm.update_preferred_domain(saved["id"], {"notes": "Official appliance source"})
    assert updated["notes"] == "Official appliance source"

    assert sm.delete_preferred_domain(saved["id"]) is True
    assert sm.list_preferred_domains(query="subzero") == []


def test_preferred_domain_hint_uses_successful_product_source(monkeypatch, tmp_path):
    sm = _isolate_source_memory(monkeypatch, tmp_path)

    sm.upsert_product_source(
        {
            "brand": "Miele",
            "model_sku": "G7986SCVIK20",
            "product_page_url": "https://www.mieleusa.com/e/g7986scvik20",
            "source_domain": "mieleusa.com",
            "source_type": "manufacturer",
            "confidence_score": 90,
            "success_count": 3,
        }
    )

    assert sm.preferred_domain_hint("Miele") == "mieleusa.com"
