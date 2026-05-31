import json
from unittest.mock import patch

from src.product_image_extraction import (
    extract_product_image_candidates,
    select_best_product_image,
    top_candidate_diagnostics,
)


def _row():
    return {
        "Brand": "Wolf",
        "Model/SKU": "MDD30TS",
        "Product Name": "Drawer Microwave",
    }


def test_collects_and_scores_json_ld_product_image_over_default_meta():
    html = """
    <meta property="og:image" content="https://wolfappliance.com/default-meta-image.jpg">
    <script type="application/ld+json">
    {"@type":"Product","name":"Wolf MDD30TS","image":"https://wolfappliance.com/images/MDD30TS-product.jpg"}
    </script>
    """

    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/mdd30ts", _row())
    selected = select_best_product_image(candidates, content_type_checker=lambda url: True)

    assert selected is not None
    assert selected.url == "https://wolfappliance.com/images/MDD30TS-product.jpg"
    assert selected.confidence == "high"
    assert any(c.rejection_reason for c in candidates if "default-meta-image" in c.url)


def test_collects_next_data_and_product_gallery_candidates():
    next_data = {
        "props": {
            "pageProps": {
                "product": {
                    "media": [
                        {"url": "https://wolfappliance.com/media/MDD30TS-gallery.webp"},
                    ]
                }
            }
        }
    }
    html = f"""
    <script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>
    <div class="product-gallery">
      <img data-zoom="/images/MDD30TS-zoom.jpg" width="1200" height="900" alt="Wolf MDD30TS drawer microwave">
    </div>
    """

    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/product/mdd30ts", _row())
    urls = {candidate.url for candidate in candidates}

    assert "https://wolfappliance.com/media/MDD30TS-gallery.webp" in urls
    assert "https://wolfappliance.com/images/MDD30TS-zoom.jpg" in urls
    assert candidates[0].score >= candidates[-1].score


def test_collects_source_srcset_and_css_background_images():
    html = """
    <picture>
      <source srcset="https://wolfappliance.com/img/MDD30TS-sm.jpg 400w, https://wolfappliance.com/img/MDD30TS-lg.jpg 1400w">
      <img src="https://wolfappliance.com/img/fallback.jpg">
    </picture>
    <div class="product-media" style="background-image:url('/media/MDD30TS-hero.png')"></div>
    """

    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/mdd30ts", _row())
    urls = {candidate.url for candidate in candidates}

    assert "https://wolfappliance.com/img/MDD30TS-lg.jpg" in urls
    assert "https://wolfappliance.com/media/MDD30TS-hero.png" in urls


def test_select_best_rejects_invalid_content_type_and_uses_next_candidate():
    html = """
    <img src="https://wolfappliance.com/img/MDD30TS-broken.jpg" width="1200" height="900" alt="Wolf MDD30TS">
    <img src="https://wolfappliance.com/img/MDD30TS-good.jpg" width="900" height="700" alt="Wolf MDD30TS product">
    """
    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/mdd30ts", _row())

    selected = select_best_product_image(
        candidates,
        content_type_checker=lambda url: not url.endswith("broken.jpg"),
    )

    assert selected is not None
    assert selected.url.endswith("good.jpg")
    rejected = [c for c in candidates if c.url.endswith("broken.jpg")][0]
    assert rejected.rejection_reason == "invalid image content-type"


def test_top_candidate_diagnostics_keeps_three_candidates():
    html = "".join(
        f'<img src="https://wolfappliance.com/img/MDD30TS-{idx}.jpg" width="{800 + idx}" height="700" alt="Wolf MDD30TS">'
        for idx in range(5)
    )
    candidates = extract_product_image_candidates(html, "https://wolfappliance.com/mdd30ts", _row())

    diagnostics = top_candidate_diagnostics(candidates, limit=3)

    assert len(diagnostics) == 3
    assert {"url", "score", "confidence", "reasons"}.issubset(diagnostics[0])


def test_try_image_from_url_uploads_cloudinary_when_configured(monkeypatch):
    import src.product_enrichment as pe

    html = '<meta property="og:image" content="https://wolfappliance.com/images/MDD30TS-product.webp">'
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")

    with patch("src.product_enrichment._fetch_page_html", return_value=html), \
         patch("src.product_enrichment._check_image_content_type", return_value=True), \
         patch("src.product_enrichment.download_and_convert_image", return_value={
             "image_status": "downloaded",
             "jpeg_bytes": b"jpeg",
             "local_image_filename": "wolf_mdd30ts.jpg",
         }), \
         patch("src.product_enrichment._upload_image_to_cloudinary", return_value="https://res.cloudinary.com/demo/image/upload/wolf_mdd30ts.jpg"):
        image_url, debug = pe._try_image_from_url(
            "https://wolfappliance.com/mdd30ts",
            row=_row(),
            return_debug=True,
        )

    assert image_url == "https://res.cloudinary.com/demo/image/upload/wolf_mdd30ts.jpg"
    assert debug["original_image_url"] == "https://wolfappliance.com/images/MDD30TS-product.webp"
    assert debug["cloudinary_status"] == "uploaded"
    assert debug["image_confidence"] == "high"
