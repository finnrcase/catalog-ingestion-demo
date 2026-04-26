import pytest
from src.automation_logs import make_log_entry


def test_make_log_entry_default_product_name():
    entry = make_log_entry("https://example.com", "success", "ok")
    assert entry["product_name"] == ""
    assert entry["product_url"] == "https://example.com"
    assert entry["status"] == "success"
    assert entry["message"] == "ok"
    assert "timestamp" in entry
    assert entry["screenshot_path"] == ""


def test_make_log_entry_with_product_name():
    entry = make_log_entry("", "filled_awaiting_confirm", "done", product_name="Wolf Microwave")
    assert entry["product_name"] == "Wolf Microwave"
    assert entry["product_url"] == ""
    assert entry["status"] == "filled_awaiting_confirm"


def test_make_log_entry_all_fields():
    entry = make_log_entry(
        "https://example.com",
        "error",
        "something went wrong",
        screenshot_path="/tmp/shot.png",
        product_name="Sub-Zero Fridge",
    )
    assert entry["product_name"] == "Sub-Zero Fridge"
    assert entry["product_url"] == "https://example.com"
    assert entry["status"] == "error"
    assert entry["message"] == "something went wrong"
    assert entry["screenshot_path"] == "/tmp/shot.png"


def test_make_log_entry_schedule_statuses():
    for status in ("schedule_nav_failed", "new_item_failed"):
        entry = make_log_entry("", status, "fallback triggered", product_name="Lamp")
        assert entry["status"] == status
        assert entry["product_name"] == "Lamp"
