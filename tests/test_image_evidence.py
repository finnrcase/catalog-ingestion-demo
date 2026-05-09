"""Tests for src/image_evidence.py — text matching helpers for confidence scoring."""
from __future__ import annotations

import pytest

from src.image_evidence import (
    sku_appears_in_text,
    product_name_appears_in_text,
    is_official_domain,
)


# ── sku_appears_in_text ───────────────────────────────────────────────────────

def test_sku_exact_match():
    assert sku_appears_in_text("MDD30TS", "Wolf MDD30TS warming drawer") is True


def test_sku_case_insensitive():
    assert sku_appears_in_text("MDD30TS", "wolf mdd30ts warming drawer") is True


def test_sku_with_dash_separator():
    assert sku_appears_in_text("MDD30TS", "Wolf MDD-30TS warming drawer") is True


def test_sku_with_space_separator():
    assert sku_appears_in_text("MDD30TS", "Wolf MDD 30 TS warming drawer") is True


def test_sku_not_substring_match():
    """SKU 'AB12' should not match inside 'CAB1234'."""
    assert sku_appears_in_text("AB12", "CAB1234 unrelated product") is False


def test_sku_with_trailing_punctuation():
    assert sku_appears_in_text("MDD30TS", "Model: MDD30TS. Built-in.") is True


def test_sku_blank_returns_false():
    assert sku_appears_in_text("", "anything") is False
    assert sku_appears_in_text("MDD30TS", "") is False


# ── product_name_appears_in_text ──────────────────────────────────────────────

def test_product_name_full_match():
    assert product_name_appears_in_text(
        "Wolf 30 Inch Warming Drawer",
        "Buy the Wolf 30 Inch Warming Drawer here.",
    ) is True


def test_product_name_partial_match_majority_tokens():
    """If 3 of 4 meaningful tokens match, that's a positive."""
    assert product_name_appears_in_text(
        "Wolf 30 Inch Warming Drawer",
        "Wolf Warming Drawer 30",
    ) is True


def test_product_name_too_few_tokens_is_false():
    """Only 1 of 4 tokens matching is too weak."""
    assert product_name_appears_in_text(
        "Wolf 30 Inch Warming Drawer",
        "completely unrelated text about teapots",
    ) is False


def test_product_name_blank_returns_false():
    assert product_name_appears_in_text("", "anything") is False


# ── is_official_domain ────────────────────────────────────────────────────────

def test_official_domain_exact():
    """Wolf appliances live on subzero-wolf.com per manufacturer_domains."""
    assert is_official_domain("https://www.subzero-wolf.com/wolf/cooking/warming-drawer", "Wolf") is True


def test_official_domain_subdomain():
    assert is_official_domain("https://images.subzero-wolf.com/img/x.jpg", "Wolf") is True


def test_official_domain_mismatch():
    assert is_official_domain("https://amazon.com/x", "Wolf") is False


def test_official_domain_unknown_brand_returns_false():
    """Brands without a manufacturer_domains entry can't be confirmed official."""
    assert is_official_domain("https://example.com/x", "MadeUpBrand123") is False


def test_official_domain_blank_inputs():
    assert is_official_domain("", "Wolf") is False
    assert is_official_domain("https://example.com", "") is False
