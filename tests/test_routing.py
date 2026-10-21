"""Panel assertions go only to the documents their panel cites (registry `sources`)."""
from verifier.llm_schemas import Assertion, Interpretation
from verifier.pipeline import _assertions_for_doc


def a(id_, origin, ref):
    return Assertion(id=id_, origin=origin, origin_ref=ref, claim_type="qualitative", statement=id_,
                     subject="s", predicate="p", check_category="figure")


INTERP = Interpretation(interpretation_ok=True, has_propositional_or=False, assertions=[
    a("A1", "text", "weight benefit"), a("A2", "population", "T2DM"),
    a("A3", "panel", "P-lixilan"), a("A4", "panel", "P-solimix"), a("A5", "panel", "P-uncited")])
PANELS = [{"panel_id": "P-lixilan", "sources": ["LixiLan-L"]}, {"panel_id": "P-solimix", "sources": ["SoliMix"]},
          {"panel_id": "P-uncited", "sources": ["SomeOtherPaper"]}]
CITED = ["LixiLan-L", "SoliMix", "Tabak"]


def ids(doc_id):
    return [x.id for x in _assertions_for_doc(INTERP, doc_id, PANELS, CITED)]


def test_each_document_gets_text_assertions_and_only_its_own_panels():
    assert ids("LixiLan-L") == ["A1", "A2", "A3", "A5"]
    assert ids("SoliMix") == ["A1", "A2", "A4", "A5"]


def test_a_panel_whose_sources_are_not_cited_goes_to_every_cited_document():
    assert "A5" in ids("Tabak")
    assert "A3" not in ids("Tabak")


def test_panels_without_sources_go_everywhere():
    assert [x.id for x in _assertions_for_doc(INTERP, "Tabak", [], CITED)] == ["A1", "A2", "A3", "A4", "A5"]
