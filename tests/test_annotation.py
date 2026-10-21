import pytest

from verifier.annotation import (AnnotationRecord, agreement, cohen_kappa, dependency_components,
                                 krippendorff_alpha_nominal, validate_record)


def record(**over):
    base = {
        "schema_version": "annotation-v1",
        "claim": {"claim_id": "P1", "text": "Drug X lowered HbA1c by 1.0% vs placebo"},
        "scope": "claim_only", "source_stratum": "assignment_style", "family_ids": ["TRIAL-A"],
        "split": "unassigned", "agreement_stratum": "representative",
        "assertions": [{"assertion_id": "A1", "origin": "text", "origin_ref": "lowered HbA1c by 1.0%",
                        "statement": "Drug X lowered HbA1c by 1.0% vs placebo", "check_category": "figure",
                        "corpus_relation": "entailed", "corpus_flags": []}],
        "evidence": [{"evidence_id": "E1", "doc_id": "TrialA", "sha256": "0" * 64, "page": 3,
                      "source_kind": "text_layer", "literal_quote": "HbA1c fell by 1.0%"}],
        "sufficient_evidence_sets": [["E1"]],
        "bundles": [{"bundle_id": "B1", "kind": "gold", "evidence_ids": ["E1"],
                     "labels": [{"assertion_id": "A1", "relation": "entailed", "flags": []}]}],
        "corpus_verdict": "supported",
        "annotator": "ann1", "annotator_role": "annotator", "independent": True, "model_prelabels_shown": False,
        "seconds_spent": 240,
    }
    base.update(over)
    return base


def test_valid_record_passes():
    assert validate_record(record()) == []


def test_bundle_must_label_every_assertion():
    r = record(bundles=[{"bundle_id": "B1", "kind": "gold", "evidence_ids": ["E1"], "labels": []}])
    assert any("B1" in p and "A1" in p for p in validate_record(r))


def test_unknown_evidence_reference_is_reported():
    r = record(sufficient_evidence_sets=[["E9"]])
    assert any("E9" in p for p in validate_record(r))


def test_verdict_must_follow_two_layer_logic():
    r = record(corpus_verdict="overstated")
    assert any("corpus_verdict" in p for p in validate_record(r))


def test_independent_sample_cannot_show_model_prelabels():
    r = record(model_prelabels_shown=True)
    assert any("prelabel" in p for p in validate_record(r))


def test_schema_rejects_operational_label():
    with pytest.raises(Exception):
        AnnotationRecord.model_validate(record(corpus_verdict="api_timeout"))


def test_dependency_components_merge_shared_participants_and_variants():
    recs = [{"claim": {"claim_id": "a"}, "family_ids": ["LixiLan-L"]},
            {"claim": {"claim_id": "b"}, "family_ids": ["LixiLan-L", "GetGoal Duo-2"]},
            {"claim": {"claim_id": "c"}, "family_ids": ["GetGoal Duo-2"]},
            {"claim": {"claim_id": "d"}, "family_ids": ["SoliMix"]},
            {"claim": {"claim_id": "e"}, "family_ids": ["Other"], "variant_of": "d"}]
    comps = dependency_components(recs)
    assert comps["a"] == comps["b"] == comps["c"]
    assert comps["d"] == comps["e"] != comps["a"]


def test_cohen_kappa_hand_computed():
    a = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    b = [1, 1, 1, 1, 0, 0, 0, 0, 0, 1]
    assert cohen_kappa(a, b) == pytest.approx(0.6)


def test_krippendorff_alpha_hand_computed():
    a = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    b = [1, 1, 1, 1, 0, 0, 0, 0, 0, 1]
    assert krippendorff_alpha_nominal(a, b) == pytest.approx(0.62)


def test_agreement_report_has_counts_raw_and_per_class_disagreements():
    rep = agreement(["supported", "overstated", "cannot_verify", "supported"],
                    ["supported", "cannot_verify", "cannot_verify", "supported"])
    assert rep["n"] == 4 and rep["raw_agreement"] == pytest.approx(0.75)
    assert rep["class_counts"]["a"]["supported"] == 2
    assert rep["disagreements"] == {"overstated->cannot_verify": 1}
