import pytest

from verifier.enums import Relation as R
from verifier.logic import And, Leaf, Not, Or, decide, evaluate

E, C, U = R.ENTAILED, R.CONTRADICTED, R.UNKNOWN


@pytest.mark.parametrize("a,b,expected", [
    (E, E, E), (E, U, U), (E, C, C), (U, C, C), (U, U, U), (C, C, C),
])
def test_kleene_and(a, b, expected):
    assert evaluate(And([Leaf("a", a), Leaf("b", b)])) is expected


@pytest.mark.parametrize("a,b,expected", [
    (E, C, E), (U, C, U), (C, C, C), (U, U, U), (E, U, E),
])
def test_kleene_or(a, b, expected):
    assert evaluate(Or([Leaf("a", a), Leaf("b", b)], kind="enumeration")) is expected


@pytest.mark.parametrize("a,expected", [(E, C), (C, E), (U, U)])
def test_kleene_not(a, expected):
    assert evaluate(Not(Leaf("a", a))) is expected


def test_not_over_flagged_generalisation_is_unknown_never_supported():
    # Trial enrolled only ages 18-65; "benefits every adult age group" is an unsupported
    # generalisation, not a contradiction. Its negation must not become supported.
    broad = Leaf("every_age_group", U, flags={"unsupported_generalization"})
    assert evaluate(Not(broad)) is U
    assert decide(Not(broad)).verdict == "cannot_verify"


def test_not_over_entailed_but_flagged_node_is_unknown():
    node = Leaf("x", E, flags={"label_scope_mismatch"})
    assert evaluate(Not(node)) is U


def test_asserted_material_flag_makes_overstated_even_if_studied_part_entailed():
    d = decide(Leaf("indication", E, flags={"label_scope_mismatch"}))
    assert d.verdict == "overstated"
    assert d.flagged == ["indication"]


def test_contradiction_plus_missing_assertion_is_overstated_with_unresolved_listed():
    d = decide(And([Leaf("value", C), Leaf("population", U)]))
    assert d.verdict == "overstated"
    assert d.unresolved == ["population"]


def test_enumerated_disjunction_distributes_over_members():
    # "insufficiently controlled on basal insulin or oral agents" covers each listed subgroup
    expr = And([Leaf("on_basal_insulin", E, flags={"label_scope_mismatch"}),
                Leaf("on_oral_agents", E, flags={"label_scope_mismatch"})], kind="enumeration")
    assert decide(expr).verdict == "overstated"


def test_propositional_or_is_unsupported_logic_in_v1():
    d = decide(Or([Leaf("a", E), Leaf("b", U)], kind="propositional"))
    assert (d.verdict, d.abstention_type) == ("cannot_verify", "unsupported_logic")


def test_all_entailed_with_gates_is_supported():
    assert decide(And([Leaf("a", E), Leaf("b", E)])).verdict == "supported"


def test_failed_gate_blocks_support():
    d = decide(And([Leaf("a", E)]), gate_failures=["extraction_unreliable"])
    assert (d.verdict, d.abstention_type) == ("cannot_verify", "extraction_unreliable")


def test_omitted_context_is_disclosed_not_material():
    d = decide(Leaf("comparison", E, flags={"omitted_context"}))
    assert d.verdict == "supported"
    assert d.disclosures == ["comparison: omitted_context"]


def test_conflicting_evidence_abstains_with_reason():
    d = decide(Leaf("a", U, flags={"conflicting_evidence"}))
    assert (d.verdict, d.abstention_type) == ("cannot_verify", "conflicting_evidence")


def test_failed_interpretation_abstains():
    d = decide(Leaf("a", E), interpretation_ok=False)
    assert (d.verdict, d.abstention_type) == ("cannot_verify", "ambiguous_interpretation")


def test_unknown_root_abstains_as_evidence_missing():
    assert decide(Leaf("a", U)).abstention_type == "evidence_missing"
