from src.dimensions import extract_labeled_dimensions, has_complete_3d_dimensions


def test_complete_labeled_dimensions_with_quotes():
    assert has_complete_3d_dimensions('36"W x 34.5"H x 24"D') is True


def test_complete_labeled_dimensions_without_quotes():
    assert has_complete_3d_dimensions("36 W x 34 H x 24 D") is True


def test_complete_word_labeled_dimensions():
    assert has_complete_3d_dimensions("Width 36, Height 34.5, Depth 24") is True


def test_rejects_marketing_numbers_without_dimension_labels():
    assert has_complete_3d_dimensions("36 inch fridge 42 built in 30 warming drawer") is False


def test_rejects_width_and_depth_without_height():
    assert has_complete_3d_dimensions('36"W x 24"D') is False


def test_blank_and_weird_dimension_values_do_not_throw():
    class BadString:
        def __str__(self):
            raise ValueError("boom")

    assert has_complete_3d_dimensions("") is False
    assert has_complete_3d_dimensions(None) is False
    assert has_complete_3d_dimensions(float("nan")) is False
    assert has_complete_3d_dimensions(BadString()) is False


def test_extracts_explicit_length_only():
    parts = extract_labeled_dimensions('36"W x 34"H x 24"D x 72"L')
    assert parts == {"width": "36", "height": "34", "depth": "24", "length": "72"}
