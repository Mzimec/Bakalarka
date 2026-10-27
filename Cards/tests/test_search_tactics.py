"""Shared combat legality and marginal aura value for candidate/leaf evaluation."""
import pytest
from dataclasses import replace

from benchmarks.pacifism_decisions import duplicate_pacifism, attack_pacified_creature, position, add
from game.ai.modular_agent import observation
from game.ai.state_value import StateValueEvaluator
from game.ai.simple_agent import SimpleAgent
from game.enums import ZoneType as Z
from game.cards.starter_cards import starter_catalog
from game.game_state import Card
from game.game_state.modifier import AddIntModifier
from game.stat_type import STAT_POWER
from game.ai.tactical_evaluation import redundant_restriction_aura


@pytest.mark.parametrize("alternate", [False, True])
def test_redundant_pacifism_is_not_preferred(alternate):
    assert duplicate_pacifism(alternate)["passed"]


@pytest.mark.parametrize("remove", [False, True])
def test_attack_evaluation_tracks_actual_blocking_permission(remove):
    assert attack_pacified_creature(remove)["passed"]


def test_redundant_aura_remains_a_legal_candidate():
    state, victim, aura = position()
    add(state, "Plains")
    add(state, "Plains")
    add(state, "Pacifism", Z.HAND)
    player = state.players[0]
    candidates = player.controller.candidates.generate(state, player, set())
    assert any(victim in SimpleAgent._targets(c.action) for c in candidates
               if hasattr(c.action, "action_generator"))


@pytest.mark.parametrize("extra", ["trigger", "modifier"])
def test_auras_with_additional_value_are_not_classified_as_pure_redundancy(extra):
    state, victim, aura = position()
    add(state, "Plains")
    add(state, "Plains")
    definition = starter_catalog()["Pacifism"]
    if extra == "trigger":
        definition = replace(definition, triggers=starter_catalog()["Cloudkin Seer"].triggers)
    else:
        definition = replace(definition, attach_mods={**definition.attach_mods, STAT_POWER: (AddIntModifier(1),)})
    player = state.players[0]
    spell = Card(definition, player)
    player.add_card(spell, Z.HAND)
    candidates = player.controller.candidates.generate(state, player, set())
    actions = [c.action for c in candidates if getattr(c.action, "source", None) is spell]
    assert actions
    assert not any(redundant_restriction_aura(state, a) for a in actions)


def test_leaf_value_discounts_pacified_combat_but_retains_noncombat_abilities():
    state, victim, aura = position()
    evaluator = StateValueEvaluator()
    view = observation(state, state.players[0])
    pacified = next(c for c in view["battlefield"] if c["id"] == victim.command_id)
    pacified_value = evaluator.permanent_value(pacified)
    aura.owner.move_card(aura, Z.GRAVEYARD, state)
    free = next(c for c in observation(state, state.players[0])["battlefield"] if c["id"] == victim.command_id)
    assert 0 < pacified_value < evaluator.permanent_value(free)
    with_ability = {**pacified, "triggers": [{"key": "draw_each_turn"}]}
    assert evaluator.permanent_value(with_ability) > pacified_value
    mana_dork = {**pacified, "abilities": [{"key": "add_green", "mana_ability": True}]}
    assert evaluator.permanent_value(mana_dork) > pacified_value
    assert -1 < evaluator.evaluate(view) < 1


def test_terminal_leaf_scores_override_material():
    state, victim, aura = position()
    view = observation(state, state.players[0])
    evaluator = StateValueEvaluator()
    view["players"][1]["has_lost"] = True
    assert evaluator.evaluate(view) == 1
    view["players"][1]["has_lost"] = False
    view["players"][0]["has_lost"] = True
    assert evaluator.evaluate(view) == -1
    view["players"][1]["has_lost"] = True
    assert evaluator.evaluate(view) == 0
