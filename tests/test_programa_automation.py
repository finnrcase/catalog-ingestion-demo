import pytest
from unittest.mock import MagicMock, patch, call
from src.programa_automation import (
    _is_url_row,
    _process_schedule_row,
    SCHEDULE_TEXTS,
    NEW_ITEM_TEXTS,
    SCHEDULE_FIELD_LABELS,
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


def test_process_schedule_row_attempts_schedule_tab():
    page = _make_page()
    with patch("src.programa_automation._click_by_text", return_value=True) as mock_click, \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    first_click_texts = mock_click.call_args_list[0][0][1]
    assert any(t in SCHEDULE_TEXTS for t in first_click_texts)


def test_process_schedule_row_attempts_new_item():
    page = _make_page()
    click_calls = []

    def fake_click(page, texts, **kw):
        click_calls.append(texts)
        return True

    with patch("src.programa_automation._click_by_text", side_effect=fake_click), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    all_texts = [t for call_texts in click_calls for t in call_texts]
    assert any(t in NEW_ITEM_TEXTS for t in all_texts)


def test_process_schedule_row_fills_product_name():
    page = _make_page()
    filled: dict[str, str] = {}

    def fake_fill(page, labels, value):
        for lbl in labels:
            filled[lbl] = value
        return True

    with patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation._fill_field_by_label", side_effect=fake_fill), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    product_name_labels = SCHEDULE_FIELD_LABELS["Product Name"]
    assert any(lbl in filled for lbl in product_name_labels)
    assert filled.get(product_name_labels[0]) == "Wolf Microwave"


def test_process_schedule_row_returns_filled_awaiting_confirm_when_not_auto_done():
    page = _make_page()
    with patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        entry = _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    assert entry["status"] == "filled_awaiting_confirm"
    assert entry["product_name"] == "Wolf Microwave"
    assert entry["product_url"] == ""


def test_process_schedule_row_returns_success_when_auto_done():
    page = _make_page()
    with patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"), \
         patch("src.programa_automation.take_screenshot", return_value=""):
        entry = _process_schedule_row(page, _schedule_row(), auto_done=True, index=1, total=1)

    assert entry["status"] == "success"
    assert entry["product_name"] == "Wolf Microwave"


def test_process_schedule_row_schedule_nav_failed_prompts_user():
    page = _make_page()
    click_calls = []

    def fake_click(page, texts, **kw):
        if any(t in SCHEDULE_TEXTS for t in texts):
            return False
        return True

    dialog_messages = []

    def fake_confirm(page, msg):
        dialog_messages.append(msg)

    with patch("src.programa_automation._click_by_text", side_effect=fake_click), \
         patch("src.programa_automation._fill_field_by_label", return_value=True), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm", side_effect=fake_confirm), \
         patch("src.programa_automation.take_screenshot", return_value="shot.png"):
        entry = _process_schedule_row(page, _schedule_row(), auto_done=False, index=1, total=1)

    assert any("Schedule" in msg or "schedule" in msg.lower() for msg in dialog_messages)


def test_process_schedule_row_extracts_material_from_notes():
    page = _make_page()
    filled: dict[str, str] = {}

    def fake_fill(page, labels, value):
        for lbl in labels:
            filled[lbl] = value
        return True

    row = _schedule_row(Notes="Some info [Materials: Solid Oak] extra")
    with patch("src.programa_automation._click_by_text", return_value=True), \
         patch("src.programa_automation._fill_field_by_label", side_effect=fake_fill), \
         patch("src.programa_automation._inject_banner"), \
         patch("src.programa_automation._remove_banner"), \
         patch("src.programa_automation._js_confirm"):
        _process_schedule_row(page, row, auto_done=False, index=1, total=1)

    material_labels = SCHEDULE_FIELD_LABELS["Material"]
    assert any(filled.get(lbl) == "Solid Oak" for lbl in material_labels)
