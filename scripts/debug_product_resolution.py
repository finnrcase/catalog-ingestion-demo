#!/usr/bin/env python3
"""Debug one product resolution without running the app."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.product_resolver import resolve_product_page


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug SCH product page/spec/image resolution.")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--sku", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--supplier", default="")
    args = parser.parse_args()

    row = {
        "Brand": args.brand,
        "Model/SKU": args.sku,
        "Product Name": args.name,
        "Supplier": args.supplier,
    }
    result = resolve_product_page(row)
    selected = result.selected

    print("Queries tried:")
    for query in result.queries_tried:
        print(f"- {query}")

    print("\nURLs checked:")
    for url in result.urls_checked:
        print(f"- {url}")

    print("\nSelected URL:", result.selected_url or "(none)")
    print("Confidence:", result.confidence)
    print("Evidence score:", result.evidence_score)
    print("Dimensions found:", selected.extracted_dimensions if selected else "")
    print("Image candidates found:", selected.diagnostics.get("image_candidates_found", 0) if selected else 0)
    print("Selected image URL:", selected.extracted_image_url if selected else "")

    print("\nCandidate diagnostics:")
    print(json.dumps(result.diagnostics, indent=2))

    if not selected:
        print("\nNo HIGH/MEDIUM verified candidate selected.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
