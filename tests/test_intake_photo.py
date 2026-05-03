from src.intake import build_intake_dataframe, create_photo_rows
from src.photo_inventory import PHOTO_ONLY_NOTE, create_photo_inventory_row, filename_to_product_name


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
    assert row["Color"] == "Black and natural"
    assert row["Material"] == "Woven fiber and wood"
    assert PHOTO_ONLY_NOTE in row["Notes"]


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
