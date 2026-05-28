from src.measurement_parser import combined_dimensions, normalize_dimension_fields, parse_dimensions


def test_parse_number_first_labeled_dimensions():
    parts = parse_dimensions('36"W x 18"D x 72"H')
    assert parts["width"] == "36"
    assert parts["depth"] == "18"
    assert parts["height"] == "72"


def test_parse_axis_first_dimensions():
    parts = parse_dimensions("W 36 x D 18 x H 72")
    assert parts["width"] == "36"
    assert parts["depth"] == "18"
    assert parts["height"] == "72"


def test_parse_colon_labeled_dimensions():
    parts = parse_dimensions("Width: 36 in, Depth: 18 in, Height: 72 in")
    assert parts["width"] == "36"
    assert parts["depth"] == "18"
    assert parts["height"] == "72"


def test_parse_diameter():
    assert parse_dimensions('24" Dia.')["diameter"] == "24"


def test_convert_feet_cm_mm_to_inches():
    assert parse_dimensions("Width: 3 ft, Depth: 45.72 cm, Height: 1828.8 mm") == {
        "width": "36",
        "height": "72",
        "depth": "18",
        "length": "",
        "diameter": "",
    }


def test_parse_mixed_fraction():
    assert parse_dimensions('12 1/2"W x 18"D x 30"H')["width"] == "12.5"


def test_combined_dimensions_from_axes():
    row, _debug = normalize_dimension_fields({
        "Dimensions": "",
        "Width (in)": "36",
        "Depth (in)": "18",
        "Height (in)": "72",
    })
    assert row["Dimensions"] == '36"W x 18"D x 72"H'


def test_axis_fields_from_combined_dimensions():
    row, _debug = normalize_dimension_fields({
        "Dimensions": '36"W x 18"D x 72"H',
        "Width (in)": "",
        "Depth (in)": "",
        "Height (in)": "",
    })
    assert row["Width (in)"] == "36"
    assert row["Depth (in)"] == "18"
    assert row["Height (in)"] == "72"
