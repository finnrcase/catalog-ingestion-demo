"""
Product enrichment orchestrator for SCH DesignOps Intake.

Automatically fills blank fields (Product Name, Dimensions, Finish / Color,
Product Category, Product URL, Notes/materials) using Brave Search + httpx +
Claude Haiku. Never overwrites existing data.

Public API
----------
enrich_row(row: dict) -> tuple[dict, str | None]
enrich_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]
"""

import json
import os
import re
import time

import httpx
import pandas as pd
from dotenv import load_dotenv

from src.brave_search import BRAVE_API_KEY, search_product_candidates
from src.category_ai import _normalise_category

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

_ENRICHABLE_FIELDS: list = [
    "Product Name",
    "Dimensions",
    "Finish / Color",
    "Product Category",
    "Product URL",
]

MIN_USE_SCORE = 40   # below this: skip entirely, note in Notes
MIN_CONF_SCORE = 60  # 40–59: fill fields but force Review Required = True


def _qualifies(row: dict) -> bool:
    """True if this row should be sent through enrichment."""
    source = str(row.get("Source Type", "") or "").strip()
    if source == "URL":
        return False
    if source.endswith("_Enriched"):
        return False
    if not str(row.get("Brand", "") or "").strip():
        return False
    if not str(row.get("Model/SKU", "") or "").strip():
        return False
    blank = [
        f for f in ["Product Name", "Dimensions", "Finish / Color", "Product Category"]
        if not str(row.get(f, "") or "").strip()
    ]
    return bool(blank)


def _build_search_query(row: dict) -> str:
    """Build a Brave Search query from the row's Brand, Model/SKU, and Product Name."""
    parts = [
        str(row.get("Brand", "") or "").strip(),
        str(row.get("Model/SKU", "") or "").strip(),
        str(row.get("Product Name", "") or "").strip(),
        "specifications official",
    ]
    return " ".join(p for p in parts if p)


# Stubs — implemented in later tasks
def _apply_enrichment(row, extracted, source_url, domain_score):
    raise NotImplementedError


def enrich_row(row):
    raise NotImplementedError


def enrich_dataframe(df):
    raise NotImplementedError
