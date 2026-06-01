from src.intake import dedupe_intake_rows
from src.intake_schema import make_base_row


def _row(**overrides):
    row = make_base_row(project="1 Lily Pond", room="", supplier="PC Richard", notes="")
    row.update({
        "Include": True,
        "Product Category": "Appliances",
        "Source Type": "PDF",
        "Status": "Needs Enrichment",
    })
    row.update(overrides)
    return row


def test_dedupe_late_pc_richard_fallback_rows_keeps_richer_rows():
    rows = [
        _row(
            Brand="Miele",
            **{
                "Model/SKU": "G7986SCVIK20",
                "Product Name": "Fully Integrated Dishwasher",
                "Room": "Kitchen",
                "Finish / Color": "PNL",
                "Dimensions": '23.625"W x 33.75"H x 21.75"D',
                "Image URL": "https://res.cloudinary.com/demo/miele.jpg",
                "Product URL": "https://www.mieleusa.com/e/g7986scvik20",
            },
        ),
        _row(
            Brand="miele",
            **{
                "Model/SKU": "G7986SCVIK20",
                "Product Name": "miele g7986scvik20 kitchen pnl",
                "Room": "",
                "Finish / Color": "pnl",
                "Dimensions": "",
                "Image URL": "",
                "Product URL": "",
            },
        ),
        _row(
            Brand="Wolf",
            **{
                "Model/SKU": "WWD30",
                "Product Name": "Warming Drawer",
                "Room": "Kitchen",
                "Finish / Color": "SS",
            },
        ),
        _row(
            Brand="wolf",
            **{
                "Model/SKU": "WWD30",
                "Product Name": "wolf wwd30 kitchen ss",
                "Room": "",
                "Finish / Color": "ss",
            },
        ),
    ]

    deduped = dedupe_intake_rows(rows)

    assert len(deduped) == 2
    by_model = {row["Model/SKU"]: row for row in deduped}
    assert by_model["G7986SCVIK20"]["Product Name"] == "Fully Integrated Dishwasher"
    assert by_model["G7986SCVIK20"]["Room"] == "Kitchen"
    assert by_model["G7986SCVIK20"]["Image URL"].startswith("https://res.cloudinary.com")
    assert by_model["WWD30"]["Product Name"] == "Warming Drawer"
    assert by_model["WWD30"]["Room"] == "Kitchen"


def test_dedupe_preserves_same_model_in_distinct_rooms():
    rows = [
        _row(Brand="Wolf", **{"Model/SKU": "WWD30", "Product Name": "Warming Drawer", "Room": "Kitchen"}),
        _row(Brand="Wolf", **{"Model/SKU": "WWD30", "Product Name": "Warming Drawer", "Room": "Pantry"}),
    ]

    deduped = dedupe_intake_rows(rows)

    assert len(deduped) == 2
    assert {row["Room"] for row in deduped} == {"Kitchen", "Pantry"}


def test_dedupe_keeps_unresolved_charge_for_parse_qa_but_excluded():
    charge = _row(
        Include=False,
        **{
            "Import Type": "unresolved_charge",
            "Product Name": "",
            "Brand": "",
            "Model/SKU": "",
            "Price": "$285.97",
        },
    )

    deduped = dedupe_intake_rows([charge])

    assert len(deduped) == 1
    assert deduped[0]["Include"] is False
    assert deduped[0]["Import Type"] == "unresolved_charge"
