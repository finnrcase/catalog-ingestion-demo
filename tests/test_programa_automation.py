import pytest
from unittest.mock import MagicMock, patch, call
from src.programa_automation import (
    _build_section_map,
    _discover_image_url_from_html,
    _is_url_row,
    _process_schedule_row,
    _to_decimal,
    ensure_section_exists,
    parse_dimensions_for_programa,
    SCHEDULE_TEXTS,
    NEW_ITEM_TEXTS,
    SCHEDULE_FIELD_LABELS,
    FILES_TAB_TEXTS,
    SCHEDULE_FILE_IDENTIFIERS,
)


# ── _is_url_row ────────────────────────────────────────────────────────────────

def test_is_url_row_true_when_source_url_and_has_url():
    row = {"Source Type": "URL", "Product URL": "https://example.com"}
    assert _is_url_row(row) is True


def test_is_url_row_false_when_source_pdf_ai():
    row = {"Source Type": "PDF_AI", "Product URL": ""}
    assert _is_url_row(row) is False


def test_is_url_row_false_when_url_source_but_empty_url():
    row = {"Source Type": "URL", "Product URL": ""}
    assert _is_url_row(row) is False


def test_is_url_row_false_when_url_source_whitespace_url():
    row = {"Source Type": "URL", "Product URL": "   "}
    assert _is_url_row(row) is False


def test_is_url_row_false_when_manual():
    row = {"Source Type": "Manual", "Product URL": "https://example.com"}
    assert _is_url_row(row) is False


# ── _to_decimal ────────────────────────────────────────────────────────────────

def test_to_decimal_integer_unchanged():
    assert _to_decimal("36") == "36"


def test_to_decimal_decimal_unchanged():
    assert _to_decimal("34.5") == "34.5"


def test_to_decimal_simple_fraction():
    assert _to_decimal("1/2") == "0.5"


def test_to_decimal_mixed_fraction():
    assert _to_decimal("29 7/8") == "29.875"


def test_to_decimal_mixed_fraction_quarter():
    assert _to_decimal("5 1/4") == "5.25"


# ── parse_dimensions_for_programa ──────────────────────────────────────────────

def test_parse_dims_labeled_whd():
    result = parse_dimensions_for_programa('36"W × 34.5"H × 24"D')
    assert result["width"] == "36"
    assert result["height"] == "34.5"
    assert result["depth"] == "24"
    assert result["length"] == ""


def test_parse_dims_labeled_case_insensitive():
    result = parse_dimensions_for_programa('36w x 34.5h x 24d')
    assert result["width"] == "36"
    assert result["height"] == "34.5"
    assert result["depth"] == "24"


def test_parse_dims_unlabeled_triple_is_not_guessed():
    result = parse_dimensions_for_programa("36 x 34.5 x 24")
    assert result["width"] == ""
    assert result["height"] == ""
    assert result["depth"] == ""
    assert result["length"] == ""


def test_parse_dims_mixed_fraction_labeled():
    result = parse_dimensions_for_programa('29 7/8"W × 34"H × 24"D')
    assert result["width"] == "29.875"
    assert result["height"] == "34"
    assert result["depth"] == "24"


def test_parse_dims_empty_string():
    result = parse_dimensions_for_programa("")
    assert result == {"width": "", "height": "", "depth": "", "length": ""}


def test_parse_dims_none():
    result = parse_dimensions_for_programa(None)
    assert result == {"width": "", "height": "", "depth": "", "length": ""}


# ── product image discovery ───────────────────────────────────────────────────

def test_discover_image_prefers_og_image():
    html = """
    <html><head><meta property="og:image" content="/images/product-main.jpg"></head>
    <body><img src="/logo.png"><img src="/thumb.jpg"></body></html>
    """
    url, source = _discover_image_url_from_html(html, "https://example.com/p/item")
    assert url == "https://example.com/images/product-main.jpg"
    assert source == "Extracted og:image"


def test_discover_image_skips_logo_and_uses_product_candidate():
    html = """
    <html><body>
      <img src="/brand-logo.png" width="600" height="200">
      <img src="/assets/product-hero.png" width="800" height="800" alt="product image">
    </body></html>
    """
    url, source = _discover_image_url_from_html(html, "https://example.com/products/abc")
    assert url == "https://example.com/assets/product-hero.png"
    assert "product image candidate" in source


# ── section creation / reuse ──────────────────────────────────────────────────

def test_ensure_section_exists_reuses_existing_section():
    page = _make_page()
    log_entries = []
    with patch("src.programa_automation._get_existing_sections", return_value={"appliances": "Appliances"}), \
         patch("src.programa_automation.create_section") as create:
        ok = ensure_section_exists(page, "Appliances", log_entries)

    assert ok is True
    create.assert_not_called()
    assert any("Reusing existing section" in entry["message"] for entry in log_entries)


def test_ensure_section_exists_empty_schedule_creates_first_section():
    page = _make_page()
    log_entries = []
    with patch("src.programa_automation._get_existing_sections", return_value={}), \
         patch("src.programa_automation._empty_schedule_state_detected", return_value=True), \
         patch("src.programa_automation.create_section", return_value=True) as create:
        ok = ensure_section_exists(page, "Appliances", log_entries)

    assert ok is True
    create.assert_called_once_with(page, "Appliances", log_entries)
    assert any("No sections found" in entry["message"] for entry in log_entries)


def test_build_section_map_omits_sections_that_cannot_be_created():
    page = _make_page()
    log_entries = []

    def fake_section_exists(page, section_name):
        if section_name == "Appliances":
            return True, "Appliances"
        return False, ""

    with patch("src.programa_automation._get_existing_sections", return_value={"appliances": "Appliances"}), \
         patch("src.programa_automation._section_exists", side_effect=fake_section_exists), \
         patch("src.programa_automation._ensure_section_exists", return_value=False):
        section_map = _build_section_map(page, ["Appliances", "Lighting"], log_entries)

    assert section_map == {"Appliances": "Appliances"}
    assert any("Existing sections found" in entry["message"] for entry in log_entries)
    assert any("Needed sections" in entry["message"] for entry in log_entries)
    assert any("Could not create required section 'Lighting'" in entry["message"] for entry in log_entries)


# ── _process_schedule_row ──────────────────────────────────────────────────────

def _make_page():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    return page


def _schedule_row(**overrides):
    base = {
        "Source Type": "PDF_AI",
        "Product Name": "Wolf Microwave",
        "Brand": "Wolf",
        "Dimensions": '30"W x 15"H x 17"D',
        "Quantity": 1,
        "Supplier": "AEG",
        "Finish / Color": "Stainless",
        "Notes": "",
        "Product URL": "",
    }
    base.update(overrides)
    return base


def _base_patches(nav_return=(True, "files_tab")):
    """Return context-manager patches shared by most _process_schedule_row tests."""
    return [
        patch("src.programa_automation._navigate_to_schedule_file", return_value=nav_return),
        patch("src.programa_automation._click_by_text", return_value=True),
        patch("src.programa_automation._fill_field_by_label", return_value=True),
        patch("src.programa_automation._inject_banner"),
        patch("src.programa_automation._remove_banner"),
        patch("src.programa_automation._js_confirm"),
    ]


def test_process_schedule_row_calls_navigate_to_schedule_file():
    page = _make_page()
    navigate_calls = []

    def fake_navigate(page):
        navigate_calls.append(True)
        return True, "files_tab"

    with patch("src.programa_automation._navigate_to_schedule_file", side_effect=fake_navigate), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    assert len(navigate_calls) == 1


def test_process_schedule_row_attempts_new_item():
    page = _make_page()
    click_calls = []

    def fake_click(page, texts, **kw):
        click_calls.append(texts)
        return True

    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", side_effect=fake_click), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    all_texts = [t for call_texts in click_calls for t in call_texts]
    assert any(t in NEW_ITEM_TEXTS for t in all_texts)


def test_process_schedule_row_fills_product_name():
    page = _make_page()
    details_payloads: list[dict] = []

    def fake_fill_details(page, product):
        details_payloads.append(product)
        return {"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}

    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")), \
         patch("src.programa_automation.fill_details_panel", side_effect=fake_fill_details), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        _process_schedule_row(
            page,
            _schedule_row(**{"Product Category": "Appliances"}),
            auto_done=False,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert details_payloads
    assert details_payloads[0]["Product Name"] == "Wolf Microwave"


def test_process_schedule_row_fills_separate_dimension_fields():
    page = _make_page()
    details_payloads: list[dict] = []

    def fake_fill_details(page, product):
        details_payloads.append(product)
        return {"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}

    row = _schedule_row(Dimensions='30"W x 15"H x 17"D')
    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")), \
         patch("src.programa_automation.fill_details_panel", side_effect=fake_fill_details), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        _process_schedule_row(
            page,
            row,
            auto_done=False,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert details_payloads
    assert details_payloads[0]["W"] == "30"
    assert details_payloads[0]["H"] == "15"
    assert details_payloads[0]["D"] == "17"


def test_process_schedule_row_returns_success_after_details_drawer_done():
    page = _make_page()
    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")), \
         patch("src.programa_automation.fill_details_panel", return_value={"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        entry = _process_schedule_row(
            page,
            _schedule_row(**{"Product Category": "Appliances"}),
            auto_done=False,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert entry["status"] == "success"
    assert entry["product_name"] == "Wolf Microwave"
    assert entry["product_url"] == ""


def test_process_schedule_row_returns_success_when_auto_done():
    page = _make_page()
    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")), \
         patch("src.programa_automation.fill_details_panel", return_value={"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        entry = _process_schedule_row(
            page,
            _schedule_row(**{"Product Category": "Appliances"}),
            auto_done=True,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert entry["status"] == "success"
    assert entry["product_name"] == "Wolf Microwave"


def test_process_schedule_row_uses_inline_section_custom_product_not_global_new():
    page = _make_page()

    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._navigate_to_section", return_value=True), \
         patch("src.programa_automation._create_custom_product_via_global_new") as global_new, \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")) as open_details, \
         patch("src.programa_automation._fill_product_row_inline_fields") as inline_fill, \
         patch("src.programa_automation.fill_details_panel", return_value={"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        entry = _process_schedule_row(
            page,
            _schedule_row(**{"Product Category": "Appliances"}),
            auto_done=False,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert entry["status"] == "success"
    open_details.assert_called_once()
    inline_fill.assert_not_called()
    global_new.assert_not_called()


def test_process_schedule_row_photo_only_uses_api_without_details(tmp_path):
    page = _make_page()
    image_path = tmp_path / "chair.jpg"
    image_path.write_bytes(b"fake image bytes")
    client_instances = []

    class FakeClient:
        def __init__(self, session):
            client_instances.append(self)

        def create_item(self, section_id):
            assert section_id == "section-1"
            return "item-1"

        def direct_upload_image(self, path):
            assert path == image_path
            return "signed-1"

        def update_item(self, item_id, fields, signed_id=None):
            assert item_id == "item-1"
            assert fields == {}
            assert signed_id == "signed-1"
            return True

    with patch("src.programa_automation._navigate_to_section", return_value=True), \
         patch("src.programa_automation.open_new_product_details") as open_details, \
         patch("src.programa_automation.fill_details_panel") as fill_details, \
         patch("src.programa_api.extract_session", return_value=object()), \
         patch("src.programa_api.extract_section_id", return_value="section-1"), \
         patch("src.programa_api.ProgramaAPIClient", FakeClient):
        entry = _process_schedule_row(
            page,
            {
                "Source Type": "Photo",
                "Import Type": "Photo Upload",
                "photo_only": True,
                "Local Image Path": str(image_path),
            },
            auto_done=False,
            index=1,
            total=1,
            skip_nav=True,
            target_section="Uncategorized",
            photo_index=1,
            photo_total=1,
        )

    assert entry["status"] == "success"
    assert "Photo-only API upload complete" in entry["message"]
    assert client_instances
    open_details.assert_not_called()
    fill_details.assert_not_called()


def test_process_schedule_row_schedule_nav_failed_prompts_user():
    page = _make_page()
    dialog_messages = []

    def fake_confirm(page, msg):
        dialog_messages.append(msg)

    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(False, "not_found")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm", side_effect=fake_confirm), \
         patch("src.programa_automation.take_screenshot", return_value="shot.png"):
        entry = _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    assert any("Schedule" in msg or "schedule" in msg.lower() for msg in dialog_messages)


def test_process_schedule_row_extracts_material_from_notes():
    page = _make_page()
    details_payloads: list[dict] = []

    def fake_fill_details(page, product):
        details_payloads.append(product)
        return {"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}

    row = _schedule_row(Notes="Some info [Materials: Solid Oak] extra")
    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")), \
         patch("src.programa_automation.fill_details_panel", side_effect=fake_fill_details), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        _process_schedule_row(
            page,
            row,
            auto_done=False,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert details_payloads
    assert details_payloads[0]["Material"] == "Solid Oak"


def test_process_schedule_row_moves_messy_description_to_notes():
    page = _make_page()
    details_payloads: list[dict] = []

    def fake_fill_details(page, product):
        details_payloads.append(product)
        return {"Product Name": "ok", "Done": "ok", "drawer_closed": "yes"}

    row = _schedule_row(
        **{
            "Product Category": "Appliances",
            "Product Description": "Line 1. Salesperson Jane, contact jane@example.com. Verify from source notes.",
            "Notes": "Existing extraction note.",
        }
    )
    with patch("src.programa_automation._navigate_to_schedule_file", return_value=(True, "files_tab")), \
         patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation.open_new_product_details", return_value=(True, "details ok", MagicMock(), 0, 1, "")), \
         patch("src.programa_automation.fill_details_panel", side_effect=fake_fill_details), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        _process_schedule_row(
            page,
            row,
            auto_done=False,
            index=1,
            total=1,
            target_section="Appliances",
        )

    assert details_payloads
    assert details_payloads[0]["Description"] == ""
    assert "Existing extraction note." in details_payloads[0]["Notes"]
    assert "Salesperson Jane" in details_payloads[0]["Notes"]
