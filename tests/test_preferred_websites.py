import pytest

from src.preferred_websites import (
    add_preferred_website,
    delete_preferred_website,
    list_preferred_websites,
    matching_preferred_websites,
    preferred_direct_urls_for_row,
    preferred_domains_for_row,
    record_preferred_website_result,
    update_preferred_website,
)


def test_preferred_website_crud_and_duplicate_validation(tmp_path):
    path = tmp_path / "preferred.json"

    entry = add_preferred_website(keyword="Sub-Zero", url="subzero-wolf.com/products/id36r", notes="official", path=path)

    assert entry["domain"] == "subzero-wolf.com"
    assert entry["url"] == "https://subzero-wolf.com/products/id36r"
    assert list_preferred_websites(path)[0]["keyword"] == "Sub-Zero"
    with pytest.raises(ValueError):
        add_preferred_website(keyword="Sub-Zero", url="https://subzero-wolf.com/products/id36r", path=path)

    updated = update_preferred_website(entry["id"], keyword="Sub-Zero Refrigerator Drawers", url=entry["url"], notes="updated", path=path)
    assert updated["notes"] == "updated"
    assert updated["keyword"] == "Sub-Zero Refrigerator Drawers"

    assert delete_preferred_website(entry["id"], path=path) is True
    assert list_preferred_websites(path) == []


def test_matching_preferred_websites_and_direct_urls(tmp_path):
    path = tmp_path / "preferred.json"
    add_preferred_website(keyword="icemaker", url="https://scotsman-ice.com/products/scn60pa1su", path=path)
    add_preferred_website(keyword="lighting", url="https://visualcomfort.com/", path=path)
    row = {
        "Product Name": "Scotsman Icemaker Built In Pump",
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
    }

    matches = matching_preferred_websites(row, path)
    direct_urls = preferred_direct_urls_for_row(row, path)

    assert [match["domain"] for match in matches] == ["scotsman-ice.com"]
    assert direct_urls[0]["url"] == "https://scotsman-ice.com/products/scn60pa1su"
    assert preferred_domains_for_row(row, path) == ["scotsman-ice.com"]


def test_matching_preferred_websites_uses_supplier_model_and_aliases(tmp_path):
    path = tmp_path / "preferred.json"
    add_preferred_website(
        keyword="PC Richard",
        url="https://pcrichard.com/",
        notes="aliases: P.C. Richard, PCR, appliances",
        path=path,
    )
    add_preferred_website(keyword="Visual Comfort", url="https://visualcomfort.com/", path=path)

    row = {
        "Supplier": "P.C. Richard",
        "Brand": "Scotsman",
        "Model/SKU": "SCN60PA1SU",
        "Product Name": "Icemaker built in pump",
    }

    matches = matching_preferred_websites(row, path)

    assert [match["domain"] for match in matches] == ["pcrichard.com"]


def test_record_preferred_website_result_tracks_field_success(tmp_path):
    path = tmp_path / "preferred.json"
    entry = add_preferred_website(keyword="Kohler sink", url="https://kohler.com/product/k-123", path=path)

    updated = record_preferred_website_result(
        entry_id=entry["id"],
        success=True,
        fields_found={"dimensions": True, "image": True, "price": False, "specs": True, "product_url": True},
        status="success",
        path=path,
    )

    assert updated["success_count"] == 1
    assert updated["field_success_counts"]["dimensions"] == 1
    assert updated["field_success_counts"]["image"] == 1
    assert updated["last_fields_found"]["price"] is False


def test_invalid_preferred_url_rejected(tmp_path):
    with pytest.raises(ValueError):
        add_preferred_website(keyword="Wolf", url="not a url", path=tmp_path / "preferred.json")
