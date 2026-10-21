from pathlib import Path

from verifier.panels import bind_panels
from verifier.resolve import load_materials

M = load_materials(Path(__file__).resolve().parents[1] / "data" / "materials.json")
C7 = ("Weight benefit while switching from basal insulin, shown across three trial panels "
      "(LixiLan-L, SoliMix, propensity-score-matching analysis)")


def test_named_panels_bind_in_panel_scope():
    b = bind_panels(C7, None, M, "claim_and_referenced_panels")
    assert b.triggered and not b.unresolved
    assert [p["panel_id"] for p in b.panels] == ["P3-weight-lixilan-l", "P3-weight-solimix", "P3-weight-psm"]
    assert {"P3-weight-header"} <= {c["panel_id"] for c in b.context}


def test_claim_only_scope_never_binds():
    b = bind_panels(C7, None, M, "claim_only")
    assert not b.triggered and b.panels == []


def test_claim_without_panel_reference_is_not_expanded():
    c6 = "Lower risk of level-2 hypoglycemia (ADA: <54 mg/dL) with iGlarLixi vs premix BIAsp 30 [OR 0.45 (0.28, 0.73)]"
    assert not bind_panels(c6, None, M, "claim_and_referenced_panels").triggered


def test_trial_name_alone_is_not_a_panel_reference():
    assert not bind_panels("Weight decreased in LixiLan-L", None, M, "claim_and_referenced_panels").triggered


def test_panel_count_must_match_named_count():
    b = bind_panels("shown across three trial panels (LixiLan-L, SoliMix)", None, M, "claim_and_referenced_panels")
    assert b.triggered and b.unresolved


def test_unnamed_panel_reference_is_unresolved():
    b = bind_panels("as shown in the weight panels", None, M, "claim_and_referenced_panels")
    assert b.triggered and b.unresolved


def test_explicit_panel_refs_take_precedence():
    b = bind_panels("anything", ["P2-2x-vs-basal-insulin"], M, "claim_and_referenced_panels")
    assert [p["panel_id"] for p in b.panels] == ["P2-2x-vs-basal-insulin"]


def test_unknown_explicit_panel_ref_is_unresolved():
    b = bind_panels("anything", ["P9-nope"], M, "claim_and_referenced_panels")
    assert b.unresolved == ["P9-nope"]
