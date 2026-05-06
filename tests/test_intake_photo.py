from src.intake import build_intake_dataframe, create_photo_rows
from src.photo_inventory import (
    MISSING_IMAGE_STATUS,
    PHOTO_ONLY_BULK_NOTE,
    PHOTO_ONLY_NOTE,
    build_photo_only_bulk_product_names,
    create_photo_inventory_row,
    create_photo_only_bulk_rows,
    filename_to_product_name,
    upload_image_to_cloudinary,
)


def test_filename_to_product_name_humanizes_filename():
    assert filename_to_product_name("blue-woven_stool.jpg") == "Blue Woven Stool"


def test_create_photo_inventory_row_uses_ai_draft_and_hosted_url():
    row = create_photo_inventory_row(
        {
            "image_filename": "chair.jpg",
            "local_image_path": "temp/product_images/bulk_uploads/chair.jpg",
            "image_upload_status": "Ready",
        },
        project="Project A",
        room="Living Room",
        ai_fields={
            "product_name": "Handwoven Accent Chair",
            "product_category": "Seating",
            "description": "A woven accent chair with a dark frame.",
            "color": "Black and natural",
            "material": "Woven fiber and wood",
        },
        image_url="https://res.cloudinary.com/demo/image/upload/chair.jpg",
    )

    assert row["Source Type"] == "Photo"
    assert row["Import Type"] == "Photo Inventory Upload"
    assert row["Product Name"] == "Handwoven Accent Chair"
    assert row["Brand"] == ""
    assert row["Model/SKU"] == ""
    assert row["Product URL"] == ""
    assert row["Product Category"] == "Seating"
    assert row["Image URL"].startswith("https://res.cloudinary.com/")
    assert row["Local Image Path"] == ""
    assert row["Color"] == "Black and natural"
    assert row["Material"] == "Woven fiber and wood"
    assert PHOTO_ONLY_NOTE in row["Notes"]


def test_create_photo_only_bulk_rows_acceptance_filename_mode():
    rows = create_photo_only_bulk_rows(
        [
            {"image_filename": "lamp.jpg", "local_image_path": "temp/lamp.jpg"},
            {"image_filename": "handmade_doll.jpg", "local_image_path": "temp/handmade_doll.jpg"},
            {"image_filename": "vase.jpg", "local_image_path": "temp/vase.jpg"},
        ],
        project="",
        room="",
        section="Decor",
        naming_mode="Filename",
        image_urls=[
            "https://res.cloudinary.com/demo/image/upload/lamp.jpg",
            "https://res.cloudinary.com/demo/image/upload/handmade_doll.jpg",
            "https://res.cloudinary.com/demo/image/upload/vase.jpg",
        ],
    )

    assert [row["Product Category"] for row in rows] == ["Decor", "Decor", "Decor"]
    assert [row["Product Name"] for row in rows] == ["lamp", "handmade_doll", "vase"]
    assert [row["Quantity"] for row in rows] == [1, 1, 1]
    assert all(row["Image URL"].startswith("https://res.cloudinary.com/") for row in rows)
    assert all(row["Local Image Path"] == "" for row in rows)
    assert all(row["Notes"] == PHOTO_ONLY_BULK_NOTE for row in rows)
    for row in rows:
        assert row["Brand"] == ""
        assert row["Model/SKU"] == ""
        assert row["Dimensions"] == ""
        assert row["Price"] == ""
        assert row["Supplier"] == ""
        assert row["Product URL"] == ""
        assert row["Material"] == ""
        assert row["Finish / Color"] == ""
        assert row["Color"] == ""


def test_create_photo_only_bulk_rows_generated_names():
    rows = create_photo_only_bulk_rows(
        [
            {"image_filename": "lamp.jpg"},
            {"image_filename": "vase.jpg"},
        ],
        project="",
        room="",
        section="General",
        naming_mode="Generated names",
        image_urls=["https://example.com/1.jpg", "https://example.com/2.jpg"],
    )

    assert [row["Product Name"] for row in rows] == ["Photo Item 001", "Photo Item 002"]


def test_create_photo_only_bulk_rows_uses_default_product_name_with_sequence():
    rows = create_photo_only_bulk_rows(
        [
            {"image_filename": "img1.jpg"},
            {"image_filename": "img2.jpg"},
            {"image_filename": "img3.jpg"},
        ],
        project="",
        room="",
        section="Furniture",
        naming_mode="Filename",
        image_urls=["https://example.com/1.jpg", "https://example.com/2.jpg", "https://example.com/3.jpg"],
        default_product_name="  Dining Chair  ",
        append_sequence=True,
    )

    assert [row["Product Name"] for row in rows] == ["Dining Chair 001", "Dining Chair 002", "Dining Chair 003"]
    assert [row["Product Category"] for row in rows] == ["Furniture", "Furniture", "Furniture"]
    assert [row["Quantity"] for row in rows] == [1, 1, 1]
    assert all(row["Notes"] == PHOTO_ONLY_BULK_NOTE for row in rows)


def test_create_photo_only_bulk_rows_uses_default_product_name_without_sequence():
    rows = create_photo_only_bulk_rows(
        [{"image_filename": "img1.jpg"}, {"image_filename": "img2.jpg"}],
        project="",
        room="",
        section="Furniture",
        naming_mode="Filename",
        image_urls=["https://example.com/1.jpg", "https://example.com/2.jpg"],
        default_product_name="Dining Chair",
        append_sequence=False,
    )

    assert [row["Product Name"] for row in rows] == ["Dining Chair", "Dining Chair"]


def test_create_photo_only_bulk_rows_marks_missing_image_when_upload_failed():
    rows = create_photo_only_bulk_rows(
        [{"image_filename": "img1.jpg"}],
        project="",
        room="",
        section="Furniture",
        naming_mode="Filename",
        image_urls=[""],
        upload_statuses=[MISSING_IMAGE_STATUS],
    )

    assert rows[0]["Image URL"] == ""
    assert rows[0]["Image Upload Status"] == MISSING_IMAGE_STATUS


def test_photo_only_bulk_default_name_whitespace_falls_back_to_naming_mode():
    names = build_photo_only_bulk_product_names(
        [{"image_filename": "lamp.jpg"}, {"image_filename": "vase.jpg"}],
        naming_mode="Generated names",
        default_product_name="   ",
        append_sequence=True,
    )

    assert names == ["Photo Item 001", "Photo Item 002"]


def test_upload_image_to_cloudinary_returns_secure_public_url(monkeypatch, tmp_path):
    image_path = tmp_path / "lamp.jpg"
    image_path.write_bytes(b"fake image")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    monkeypatch.setattr(
        "src.photo_inventory.upload_image",
        lambda file: "https://res.cloudinary.com/demo/image/upload/lamp.jpg",
    )
    monkeypatch.setattr("src.photo_inventory.public_https_url_is_accessible", lambda url: True)

    url, error = upload_image_to_cloudinary(str(image_path))

    assert url == "https://res.cloudinary.com/demo/image/upload/lamp.jpg"
    assert error is None


def test_upload_image_to_cloudinary_rejects_non_https_url(monkeypatch, tmp_path):
    image_path = tmp_path / "lamp.jpg"
    image_path.write_bytes(b"fake image")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    monkeypatch.setattr(
        "src.photo_inventory.upload_image",
        lambda file: "http://res.cloudinary.com/demo/image/upload/lamp.jpg",
    )

    url, error = upload_image_to_cloudinary(str(image_path))

    assert url == ""
    assert "non-HTTPS" in str(error)


def test_create_photo_rows_are_blank_products_with_image_metadata():
    rows = create_photo_rows(
        [
            {
                "image_filename": "chair.jpg",
                "local_image_path": "temp/product_images/bulk_uploads/chair.jpg",
                "image_upload_status": "Ready",
            }
        ],
        project="Project A",
        room="Living Room",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["Source Type"] == "Photo"
    assert row["Import Type"] == "Photo Upload"
    assert row["photo_only"] is True
    assert row["Product Name"] == ""
    assert row["Brand"] == ""
    assert row["Model/SKU"] == ""
    assert row["Product Category"] == ""
    assert row["Dimensions"] == ""
    assert row["Price"] == ""
    assert row["Product URL"] == ""
    assert row["Quantity"] is None
    assert row["Image Filename"] == "chair.jpg"
    assert row["Local Image Path"].endswith("chair.jpg")


def test_build_intake_dataframe_preserves_photo_image_columns():
    rows = create_photo_rows(
        [{"image_filename": "lamp.png", "local_image_path": "temp/lamp.png"}],
        project="",
        room="",
    )
    df = build_intake_dataframe([], rows)

    assert "Image Filename" in df.columns
    assert "Local Image Path" in df.columns
    assert df.loc[0, "Image Filename"] == "lamp.png"
