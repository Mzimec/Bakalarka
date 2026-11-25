"""Tactical benchmark contracts verified against resolved real-engine outcomes."""
from benchmarks.search_quality import run_case


def test_double_block_scores_damage_outcomes_and_resolves_all_nested_choices():
    row = run_case("double_block", "uniform", iterations=8)
    assert row["verification"]["passed"] and not row["verification"]["unresolved"]
    assert not row["search"]["used_fallback"]
    # All declarations used to get the same pre-damage leaf score.
    means = {e["mean_value"] for e in row["search"]["estimates"] if e["visits"]}
    assert -1 in means and len(means) > 1
    assert any(t["request"] == "ReplacementOrderRequest" for t in row["verification"]["trace"])


def test_guided_rollout_finds_two_spell_lethal_that_single_action_prior_misses():
    heuristic = run_case("two_spells_mana", "heuristic", iterations=16)
    guided = run_case("two_spells_mana", "guided", iterations=16)
    assert not heuristic["verification"]["passed"]
    assert not heuristic["verification"]["unresolved"]
    assert guided["verification"]["passed"] and not guided["search"]["used_fallback"]
    assert guided["verification"]["terminal"]
    assert guided["search"]["metrics"]["rollout_policy_choices"] > 0
    cast = [t for t in guided["verification"]["trace"] if t["action"]["kind"] == "AbilityAction"]
    assert len(cast) == 2 and all(t["action"]["source"]["name"] == "Shock" for t in cast)
