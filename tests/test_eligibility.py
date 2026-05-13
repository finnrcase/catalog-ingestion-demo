from src.eligibility import evaluate_programa_eligibility, split_eligible_rows


def test_missing_values_block_programa_send_by_default():
    row = {
        "Include": True,
        "Review Required": True,
        "Status": "Needs Review",
        "Product Name": "",
        "Quantity": "",
        "Product Category": "",
        "Dimensions": "",
    }

    ok, blockers = evaluate_programa_eligibility(row)

    assert ok is False
    assert "Needs review" in blockers
    assert "Missing product name" in blockers
    assert "Missing full W x H x D dimensions" in blockers


def test_allow_blank_fields_allows_missing_values():
    row = {
        "Include": True,
        "Review Required": True,
        "Status": "Needs Review",
        "Product Name": "",
        "Quantity": "",
        "Product Category": "",
        "Dimensions": "",
    }

    ok, blockers = evaluate_programa_eligibility(row, allow_blank_fields=True)

    assert ok is True
    assert blockers == []


def test_explicitly_ignored_rows_still_do_not_send():
    eligible, blocked = split_eligible_rows(
        [
            {"Include": True, "Status": "Needs Review", "Product Name": ""},
            {"Include": False, "Status": "Needs Review"},
            {"Include": True, "Status": "Ignored"},
        ],
        allow_blank_fields=True,
    )

    assert len(eligible) == 1
    assert len(blocked) == 2


def test_photo_only_rows_require_public_image_url():
    ok, blockers = evaluate_programa_eligibility(
        {
            "Include": True,
            "Source Type": "Photo",
            "Import Type": "Photo Upload",
            "photo_only": True,
            "Product Name": "Lamp",
            "Quantity": "",
            "Product Category": "Decor",
            "Dimensions": "",
            "Image URL": "temp/lamp.jpg",
        },
        allow_blank_fields=True,
    )

    assert ok is False
    assert blockers == ["Missing image"]


def test_photo_only_rows_are_send_eligible_with_public_image_url():
    ok, blockers = evaluate_programa_eligibility(
        {
            "Include": True,
            "Source Type": "Photo",
            "Import Type": "Photo Upload",
            "photo_only": True,
            "Product Name": "Lamp",
            "Product Category": "Decor",
            "Image URL": "https://res.cloudinary.com/demo/image/upload/lamp.jpg",
        },
        allow_blank_fields=True,
    )

    assert ok is True
    assert blockers == []


def test_photo_only_ignored_rows_still_do_not_send():
    ok, blockers = evaluate_programa_eligibility(
        {
            "Include": True,
            "Source Type": "Photo",
            "Import Type": "Photo Upload",
            "photo_only": True,
            "Status": "Ignored",
        },
        allow_blank_fields=True,
    )

    assert ok is False
    assert blockers == ["Ignored or excluded", "Missing product name", "Missing category", "Missing image"]
