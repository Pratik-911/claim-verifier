from pathlib import Path

import pytest

from verifier.retrieve import candidate_pages
from verifier.sources import ingest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def docs():
    return {name: ingest(ROOT / "papers" / f"{name}.pdf", cache_dir=ROOT / "corpus")
            for name in ("SoliMix_Rosenstock2021", "LixiLan-O_Rosenstock2016", "LixiLan-L_Aroda2016_abstract")}


def test_meaning_query_finds_results_prose_page(docs):
    r = candidate_pages(docs["LixiLan-O_Rosenstock2016"], "HbA1c change from baseline Week 30 iGlarLixi iGlar reduction")
    assert 13 in r.pages


def test_numeric_anchor_page_is_included(docs):
    r = candidate_pages(docs["SoliMix_Rosenstock2021"], "level 2 hypoglycemia odds ratio", anchors=["0.45", "0.73"])
    assert 7 in r.pages
    assert any(why.startswith("anchor") for why in r.reasons[7])


def test_invented_number_does_not_prevent_meaning_retrieval(docs):
    r = candidate_pages(docs["SoliMix_Rosenstock2021"], "participants reached HbA1c target <7%", anchors=["55.5"])
    assert 6 in r.pages


def test_raster_ocr_text_is_searchable(docs):
    r = candidate_pages(docs["LixiLan-L_Aroda2016_abstract"], "mean body weight decreased")
    assert r.pages[0] == 2


def test_page_hint_always_included_and_search_recorded(docs):
    r = candidate_pages(docs["LixiLan-O_Rosenstock2016"], "weight", page_hint=29, k=2)
    assert 29 in r.pages
    assert r.searched == len(docs["LixiLan-O_Rosenstock2016"].pages)


def test_broader_fallback_excludes_already_seen(docs):
    first = candidate_pages(docs["LixiLan-O_Rosenstock2016"], "HbA1c Week 30", k=3)
    more = candidate_pages(docs["LixiLan-O_Rosenstock2016"], "HbA1c Week 30", k=3, exclude=first.pages)
    assert not set(first.pages) & set(more.pages)


def test_word_anchor_does_not_crash(docs):
    r = candidate_pages(docs["SoliMix_Rosenstock2021"], "weight", anchors=["three"])
    assert r.pages


def test_each_assertion_gets_its_own_candidates_plus_first_page(docs):
    from verifier.retrieve import candidate_pages_for_assertions
    r = candidate_pages_for_assertions(
        docs["SoliMix_Rosenstock2021"],
        ["level 2 hypoglycemia odds ratio iGlarLixi BIAsp 30",
         "adults suboptimally controlled basal insulin oral antihyperglycemic drugs randomized"],
        anchors=["0.45"])
    assert 7 in r.pages and 1 in r.pages
