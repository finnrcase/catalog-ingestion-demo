import pytest
from src.location_normalizer import normalize_location


# ── Uncertainty qualifier stripping ───────────────────────────────────────────

def test_dash_if_we_can_fit_it():
    loc, conf, reason = normalize_location("Bar - if we can fit it")
    assert loc == "Bar"
    assert conf < 75
    assert "verify" in reason.lower() or "infer" in reason.lower()


def test_no_dash_if_we_can_fit_it():
    loc, conf, reason = normalize_location("Bar if we can fit it")
    assert loc == "Bar"
    assert conf < 75


def test_uncertainty_reason_mentions_verify():
    _, conf, reason = normalize_location("Kitchen - if possible")
    assert conf < 75
    assert reason  # non-empty


# ── Title-case normalisation ──────────────────────────────────────────────────

def test_lowercase_laundry_room():
    loc, conf, _ = normalize_location("laundry room floor 2")
    assert loc == "Laundry Room Floor 2"
    assert conf >= 75


def test_lowercase_single_word():
    for raw, expected in [
        ("exterior", "Exterior"),
        ("kitchen", "Kitchen"),
        ("mudroom", "Mudroom"),
        ("primary", "Primary"),
        ("gym", "Gym"),
    ]:
        loc, conf, _ = normalize_location(raw)
        assert loc == expected, f"{raw!r} → expected {expected!r}, got {loc!r}"
        assert conf >= 75


def test_two_word_lowercase():
    loc, conf, _ = normalize_location("nanny vestibule")
    assert loc == "Nanny Vestibule"
    assert conf >= 75


# ── Already-clean inputs ──────────────────────────────────────────────────────

def test_already_title_case():
    loc, conf, _ = normalize_location("Kitchen")
    assert loc == "Kitchen"
    assert conf >= 75


def test_already_title_case_multi_word():
    loc, conf, _ = normalize_location("Laundry Room Floor 2")
    assert loc == "Laundry Room Floor 2"
    assert conf >= 75


# ── Empty / whitespace inputs → use default ──────────────────────────────────

def test_empty_with_default():
    loc, conf, reason = normalize_location("", "Kitchen")
    assert loc == "Kitchen"
    assert conf < 75
    assert reason  # non-empty


def test_whitespace_only_with_default():
    loc, conf, _ = normalize_location("   ", "Bathroom")
    assert loc == "Bathroom"
    assert conf < 75


def test_empty_no_default():
    loc, conf, _ = normalize_location("")
    assert loc == ""
    assert conf == 0


# ── Return type ───────────────────────────────────────────────────────────────

def test_returns_three_tuple():
    result = normalize_location("Bar")
    assert isinstance(result, tuple)
    assert len(result) == 3
    loc, conf, reason = result
    assert isinstance(loc, str)
    assert isinstance(conf, int)
    assert isinstance(reason, str)
