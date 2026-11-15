"""Caption text above or below a PDF image is stored with that image."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import ingest_pdf  # noqa: E402


def _solid(color: tuple[int, int, int]) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60), 0)
    pix.set_rect(pix.irect, color)
    return pix


def _page_with_figures() -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        pymupdf.Point(72, 78),
        "Figure 1. Oxygen above the burner.",
        fontsize=11,
        fontname="helv",
    )
    page.insert_image(pymupdf.Rect(72, 96, 360, 250), pixmap=_solid((40, 90, 140)))
    page.insert_image(pymupdf.Rect(72, 340, 250, 500), pixmap=_solid((140, 40, 40)))
    page.insert_image(pymupdf.Rect(270, 340, 470, 500), pixmap=_solid((40, 140, 60)))
    page.insert_text(
        pymupdf.Point(72, 518),
        "Figure 2. Two fuel bottles in the case and one assembly on the right side.",
        fontsize=11,
        fontname="helv",
    )
    page.insert_text(
        pymupdf.Point(72, 532),
        "The right-hand photo includes the valve and the quick connect fitting.",
        fontsize=11,
        fontname="helv",
    )
    page.insert_text(pymupdf.Point(560, 770), "4", fontsize=10, fontname="helv")
    page._doc = doc  # keep the document alive with the page
    return page


def test_caption_above_and_shared_caption_below():
    page = _page_with_figures()
    try:
        found = ingest_pdf.captions_by_xref(page)
        captions = {text for text, _pos in found.values()}
        above = [item for item in found.values() if item[1] == "above"]
        below = [item for item in found.values() if item[1] == "below"]
        assert any(text.startswith("Figure 1.") for text, _pos in above)
        assert any("Two fuel bottles" in text and "quick connect" in text for text, _pos in below)
        assert sum(1 for text, _pos in below if "Two fuel bottles" in text) == 2
        assert "4" not in captions
    finally:
        page.parent.close()


def test_short_title_above_image_without_figure_number():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(pymupdf.Point(80, 140), "Hot flame stage", fontsize=14, fontname="helv")
    page.insert_image(pymupdf.Rect(80, 156, 320, 340), pixmap=_solid((30, 30, 30)))
    try:
        found = ingest_pdf.captions_by_xref(page)
        assert len(found) == 1
        text, position = next(iter(found.values()))
        assert text == "Hot flame stage"
        assert position == "above"
    finally:
        doc.close()


def test_touching_slices_are_one_figure():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    colors = ((180, 40, 40), (40, 140, 40), (40, 40, 180), (180, 180, 40))
    # 80x60 pixmap, so the placed boxes keep that shape and share edges.
    rects = (
        pymupdf.Rect(80, 80, 160, 140),
        pymupdf.Rect(160, 80, 240, 140),
        pymupdf.Rect(80, 140, 160, 200),
        pymupdf.Rect(160, 140, 240, 200),
    )
    for rect, color in zip(rects, colors):
        page.insert_image(rect, pixmap=_solid(color))
    page.insert_image(pymupdf.Rect(80, 400, 240, 520), pixmap=_solid((20, 20, 20)))
    try:
        clusters = ingest_pdf._cluster_tiles(ingest_pdf._page_tiles(page))
        sliced = [cluster for cluster in clusters if ingest_pdf._is_sliced_figure(cluster)]
        assert len(sliced) == 1
        assert len(sliced[0]) == 4
    finally:
        doc.close()


def test_sliced_grid_is_saved_as_one_image(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    colors = ((180, 40, 40), (40, 140, 40), (40, 40, 180), (180, 180, 40))
    rects = (
        pymupdf.Rect(80, 80, 160, 140),
        pymupdf.Rect(160, 80, 240, 140),
        pymupdf.Rect(80, 140, 160, 200),
        pymupdf.Rect(160, 140, 240, 200),
    )
    for rect, color in zip(rects, colors):
        page.insert_image(rect, pixmap=_solid(color))
    try:
        saved = ingest_pdf.extract_page_images(
            doc, page, tmp_path, 1, psi_id="PSI-1", source="synthetic.pdf", ocr=False
        )
    finally:
        doc.close()
    import json
    assert len(saved) == 1
    meta = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert meta["stitched"] == 4


def test_full_page_text_scan_is_not_a_figure():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    words = " ".join(
        ["aspect", "ratio", "capillary", "number", "specific", "heat", "container", "diameter"] * 4
    )
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 400), words, fontsize=11, fontname="helv")
    try:
        assert ingest_pdf._is_text_page_scan(page, (0.0, 0.0, 612.0, 792.0))
        assert not ingest_pdf._is_text_page_scan(page, (80.0, 80.0, 200.0, 200.0))
    finally:
        doc.close()


def test_label_filter_drops_fragments():
    assert ingest_pdf._useful_label("Hot flame stage")
    assert ingest_pdf._useful_label("0g, -0.6 kV, gas-jet")
    assert not ingest_pdf._useful_label("(a)")
    assert not ingest_pdf._useful_label("www.nasa.gov")
    assert not ingest_pdf._useful_label("and lifted flames. To improve understanding of the")
    assert not ingest_pdf._useful_label("Background > Science > Hardware > Status")


def test_caption_tag_escapes_brackets():
    assert ingest_pdf.caption_tag("Figure 1. See [a].") == "[caption: Figure 1. See (a).]"


def test_document_dir_keeps_psi_section():
    sample = ingest_pdf.PSI / "PSI-101_SAME-R" / "Reports" / "SAMER_Final 0602 2014.pdf"
    assert ingest_pdf.document_dir(sample) == Path("PSI-101_SAME-R") / "Reports"
    assert ingest_pdf.psi_id_from_path(sample) == "PSI-101"
