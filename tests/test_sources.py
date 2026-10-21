"""Ingestion and provenance on the real supplied PDFs (OCR runs once per session via a temp cache)."""
import re
from pathlib import Path

import pytest

from verifier.sources import ingest, locate_quote

PAPERS = Path(__file__).resolve().parents[1] / "papers"


@pytest.fixture(scope="session")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("corpus")


@pytest.fixture(scope="session")
def lbl(cache):
    return ingest(PAPERS / "SOLIQUA_LBL.pdf", cache_dir=cache)


@pytest.fixture(scope="session")
def abstract(cache):
    return ingest(PAPERS / "LixiLan-L_Aroda2016_abstract.pdf", cache_dir=cache)


@pytest.fixture(scope="session")
def solimix(cache):
    return ingest(PAPERS / "SoliMix_Rosenstock2021.pdf", cache_dir=cache)


def test_every_lbl_page_has_a_raster_ocr_region(lbl):
    assert len(lbl.pages) == 4
    for page in lbl.pages:
        assert any(r.reason == "raster_no_text" for r in page.ocr_regions), page.number
    assert "adults patients with Obesity" in lbl.pages[3].ocr_text()


def test_abstract_raster_body_is_ocrd_despite_text_footer(abstract):
    p2 = abstract.pages[1]
    assert p2.raw_text.strip()  # the footer text layer exists ...
    assert "Mean body weight decreased by 0.7 kg" in p2.ocr_text()  # ... but the body is OCR'd


def test_glyph_check_flags_corrupt_minus_and_ocrs_its_block(solimix):
    p1 = solimix.pages[0]
    assert p1.flagged, "the \\x01 minus glyphs must be flagged"
    assert any(r.reason == "glyph_check" for r in p1.ocr_regions)
    loc = locate_quote(solimix, "LS mean difference -0.2%")
    assert loc is None or loc.source_kind == "ocr"


def test_symbol_font_glyph_is_flagged(cache):
    tabak = ingest(PAPERS / "Tabak2020_PSM.pdf", cache_dir=cache)
    p5 = tabak.pages[4]
    flagged_chars = {p5.raw_text[i] for i in p5.flagged}
    assert "\\" in flagged_chars


def test_clean_text_layer_quote_has_provenance(cache):
    doc = ingest(PAPERS / "LixiLan-O_Rosenstock2016.pdf", cache_dir=cache)
    loc = locate_quote(doc, "−1.63% for iGlarLixi, −1.34% for")
    assert (loc.page, loc.source_kind) == (13, "text_layer")
    assert doc.pages[12].raw_text[loc.start:loc.end] == "−1.63% for iGlarLixi, −1.34% for"
    assert loc.bbox is not None


def test_quote_in_raster_region_is_located_as_ocr(abstract):
    loc = locate_quote(abstract, "decreased by 0.7 kg with iGlarLixi")
    assert (loc.page, loc.source_kind) == (2, "ocr")
    assert loc.region_id


def test_ocr_regions_carry_crop_provenance(lbl):
    region = lbl.pages[3].ocr_regions[0]
    assert re.fullmatch(r"[0-9a-f]{64}", region.image_sha256)
    assert region.engine == "rapidocr" and region.engine_version and region.dpi == 300
    x0, y0, x1, y1 = region.bbox
    assert 0 <= x0 < x1 <= lbl.pages[3].width + 1 and 0 <= y0 < y1 <= lbl.pages[3].height + 1


def test_second_ingest_comes_from_cache(cache, lbl):
    again = ingest(PAPERS / "SOLIQUA_LBL.pdf", cache_dir=cache)
    assert again.sha256 == lbl.sha256
    assert [p.ocr_text() for p in again.pages] == [p.ocr_text() for p in lbl.pages]
    assert again.from_cache is True


def test_identifiers_survive_line_wraps(cache):
    tabak = ingest(PAPERS / "Tabak2020_PSM.pdf", cache_dir=cache)
    assert tabak.meta["doi"] == "10.1007/s13300-019-00735-7"
    assert {"NCT02058160", "NCT01768559"} <= set(tabak.meta["trial_ids"])


def test_small_print_is_ocrd_at_native_resolution(lbl):
    # the abridged PI is ~6 pt; detection must not downscale the 300 DPI render
    assert locate_quote(lbl, "to metformin with or without SGLT2 inhibitors") is not None


def test_quote_copied_with_the_unreadable_marker_locates_the_raw_glyph(solimix):
    loc = locate_quote(solimix, "type 2 diabetes (HbA1c ⟦?⟧7.5% and #10%)", page_hint=1)
    assert loc and loc.page == 1 and loc.source_kind == "text_layer"
    assert "⟦?⟧" not in loc.text and "7.5% and #10%" in loc.text


REORDERED_FIGURE_QUOTE = "Participants who reached HbA1c <7 %\n\n42.2 (n=187)\n1.65 (1.25, 2.19)\n31.8 (n=141)"


def test_figure_quote_with_reordered_lines_becomes_one_verbatim_record_per_line(solimix):
    from verifier.pipeline import _locate_evidence
    assert locate_quote(solimix, REORDERED_FIGURE_QUOTE, page_hint=6) is None  # not contiguous in the text layer
    locs = _locate_evidence(solimix, REORDERED_FIGURE_QUOTE, page=6)
    assert [loc.page for loc in locs] == [6, 6, 6, 6]
    assert [loc.text for loc in locs] == ["Participants who reached HbA1c <7 %", "42.2 (n=187)", "1.65 (1.25, 2.19)",
                                          "31.8 (n=141)"]


def test_line_fallback_fails_if_any_line_is_not_on_the_page(solimix):
    from verifier.pipeline import _locate_evidence
    assert _locate_evidence(solimix, "Participants who reached HbA1c <7 %\n42.9 (n=187)", page=6) is None
    assert _locate_evidence(solimix, "Participants who reached HbA1c <7 %\n42.2 (n=187)", page=5) is None
