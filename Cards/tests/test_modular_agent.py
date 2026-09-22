"""Regression contracts for modular decisions, model isolation and configured runs."""
from game.ai.decision_maker import DeclareAttackersRequest, PriorityDecisionRequest

import json
from pathlib import Path

import pytest

from game.ai.modular_agent import (
    ModularAgent, Candidate, CandidateGenerator, CombatPolicy, observation,
)
from game.ai.llm_policy import OllamaSelector
from game.ai.simple_agent import SimpleAgent
from game.cards.starter_cards import starter_catalog
from game.enums import ZoneType as Z, TurnPhase as P
from game.game_state import State, Player, Card
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.simulation.config import run_config, make_controller


def board():
    state = State([Player([], ModularAgent(), idx=0), Player([], SimpleAgent(), idx=1)])
    state.turn.phase = P.PRECOMBAT_MAIN
    return state


def add(state, name, zone=Z.BATTLEFIELD, player=0):
    card = Card(starter_catalog()[name], state.players[player])
    card.owner.add_card(card, zone)
    return card


def test_modular_agent_casts_discounted_spell_without_mutating_during_planning():
    state = board()
    player = state.players[0]
    add(state, "Cloudkin Seer")
    islands = [add(state, "Island") for _ in range(2)]
    words = add(state, "Winged Words", Z.HAND)
    action = player.controller.decide(PriorityDecisionRequest(state, player)).value
    assert action.source is words
    assert not any(c.is_tapped for c in islands)
    assert words.get_zone() == Z.HAND
    engine = ResolutionEngine(OperationExecutor(), EventBus())
    assert all(r.success for r in ActionProcessor(engine).process(state, action))
    assert all(c.is_tapped for c in islands)


def test_observation_does_not_expose_opponent_hand_or_libraries():
    state = board()
    add(state, "Island", Z.HAND)
    add(state, "Soulblade Djinn", Z.HAND, 1)
    add(state, "Overflowing Insight", Z.DECK)
    add(state, "Sleep", Z.DECK, 1)
    text = json.dumps(observation(state, state.players[0]))
    assert "Island" in text
    assert all(name not in text for name in ("Soulblade Djinn", "Overflowing Insight", "Sleep"))


@pytest.mark.parametrize("reply", ['{}', '{"choice": -1}', '{"choice": 8}',
                                    '{"choice": true}', 'not json'])
def test_invalid_model_reply_falls_back_to_best_prior(reply):
    selector = OllamaSelector("fake", transport=lambda payload: reply)
    choices = [Candidate(None, 1, {}), Candidate(None, 9, {})]
    assert selector.choose({}, choices) == 1
    assert selector.last_error


def test_model_budget_and_valid_choice():
    requests = []
    def transport(payload):
        requests.append(payload)
        return '{"choice": 0}'
    selector = OllamaSelector("fake", transport=transport, max_requests=1)
    choices = [Candidate(None, 1, {}), Candidate(None, 9, {})]
    assert selector.choose({}, choices) == 0
    assert selector.choose({}, choices) == 1
    assert len(requests) == 1
    assert requests[0]["stream"] is False


def test_network_failure_falls_back():
    def transport(payload):
        raise TimeoutError("test timeout")
    selector = OllamaSelector("fake", transport=transport)
    assert selector.choose({}, [Candidate(None, 0, {}), Candidate(None, 1, {})]) == 1


def test_combat_search_respects_menace_and_preserves_state():
    state = board()
    attacker = add(state, "Goblin Gang Leader")
    from game.stat_type import STAT_KEYWORDS
    attacker.set_base_stat(STAT_KEYWORDS, frozenset({"menace", "haste"}))
    add(state, "Impassioned Orator", player=1)
    add(state, "Impassioned Orator", player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.players[0], {attacker: state.players[1]})
    state.turn.phase = P.DECLARE_BLOCKERS
    candidates = CombatPolicy().blockers(state, state.players[1])
    assert any(len(c.action) == 2 for c in candidates)
    assert all(len(c.action) in (0, 2) for c in candidates)
    assert not state.combat.blockers


def test_attack_policy_avoids_obvious_losing_trade():
    state = board()
    attacker = add(state, "Cloudkin Seer")
    add(state, "Serra Angel", player=1)
    state.players[0].last_turn_started = 1
    attacker.controlled_since = 0
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    assert state.players[0].controller.decide(DeclareAttackersRequest(state, state.players[0])).value.declarations == {}


def test_injected_combat_strategy_is_validated_before_evaluation():
    from game.game_actions.generation.decision_abstraction.options import DeclareBlockersOption
    from game.stat_type import STAT_KEYWORDS
    state = board()
    attacker = add(state, "Goblin Gang Leader")
    attacker.set_base_stat(STAT_KEYWORDS, frozenset({"menace", "haste"}))
    blocker = add(state, "Impassioned Orator", player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.players[0], {attacker: state.players[1]})
    state.turn.phase = P.DECLARE_BLOCKERS
    evaluated = []

    class Proposals:
        def generate(self, request):
            yield DeclareBlockersOption({blocker: attacker})
            yield DeclareBlockersOption({})

    class Evaluator:
        def block_score(self, request, option):
            evaluated.append(option)
            return 42

    candidates = CombatPolicy(blockers_strategy=Proposals(), evaluator=Evaluator()).blockers(
        state, state.players[1],
    )
    assert evaluated == [DeclareBlockersOption({})]
    assert len(candidates) == 1 and candidates[0].score == 42
    assert state.combat.blockers == {}


def test_bounded_combat_keeps_mandatory_block_proposal_before_search_budget():
    from game.stat_type import STAT_KEYWORDS
    state = board()
    attacker = add(state, "Goblin Gang Leader")
    attacker.set_base_stat(STAT_KEYWORDS, frozenset({"must be blocked by all", "haste"}))
    blockers = [add(state, "Impassioned Orator", player=1) for _ in range(3)]
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.players[0], {attacker: state.players[1]})
    state.turn.phase = P.DECLARE_BLOCKERS
    candidates = CombatPolicy(max_assignments=1).blockers(state, state.players[1])
    assert [c.action for c in candidates] == [dict.fromkeys(blockers, attacker)]
    assert state.players[1].controller.decisions == 0
    assert state.combat.blockers == {}


@pytest.mark.parametrize("config", [
    {"type": "unknown"}, {"type": "modular", "candidates": {"target_limit": 0}},
    {"type": "modular", "combat": {"max_assignments": 0}},
    {"type": "llm"}, {"type": "modular", "typo": 1},
])
def test_bad_agent_settings_rejected(config):
    with pytest.raises(ValueError):
        make_controller(config, seed=1, max_decisions=100)


def test_config_runs_relative_deck_file_and_logs_metadata(tmp_path):
    from game.cards.decks import load_arena_starter
    deck = load_arena_starter("blue")
    (tmp_path / "deck.txt").write_text(
        "\n".join(f"{count} {name}" for name, count in deck.cards), encoding="utf-8"
    )
    config = {"seed": 11, "max_turns": 2, "players": [
        {"name": "New", "deck": {"file": "deck.txt"}, "agent": {"type": "modular"}},
        {"name": "Old", "deck": "white", "agent": {"type": "simple"}},
    ]}
    path = tmp_path / "match.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    result = run_config(path, output=tmp_path / "game.jsonl")
    assert result.status == "turn_limit", result.error
    rows = [json.loads(line) for line in (tmp_path / result.log).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["metadata"]["effective_seed"] == 11
    assert rows[0]["decklists"][0]["cards"]
    assert (tmp_path / result.log).with_suffix(".txt").exists()


def test_seeded_modular_runs_have_same_decisions(tmp_path):
    config = {"seed": 22, "max_turns": 6, "players": [
        {"deck": "blue", "agent": {"type": "modular"}},
        {"deck": "white", "agent": {"type": "modular"}},
    ]}
    path = tmp_path / "match.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    decisions = []
    for index in range(2):
        result = run_config(path, output=tmp_path / f"run{index}.jsonl")
        assert result.status == "turn_limit", result.error
        rows = [json.loads(line) for line in (tmp_path / result.log).read_text(encoding="utf-8").splitlines()]
        decisions.append([row for row in rows if row["kind"] == "decision"])
    assert decisions[0] == decisions[1]


def test_parameter_policy_proposes_only_payable_x_values():
    from dataclasses import replace
    from game.mana.mana_value import ManaValue
    state = board()
    for _ in range(3):
        add(state, "Island")
    victim = add(state, "Impassioned Orator", player=1)
    definition = replace(starter_catalog()["Unsummon"],
                         mana_cost=ManaValue("{X}{U}").to_immutable())
    spell = Card(definition, state.players[0])
    spell.owner.add_card(spell, Z.HAND)
    proposals = CandidateGenerator(keep_per_ability=20).generate(state, state.players[0], set())
    values = {c.description["x"] for c in proposals if c.key is not None}
    assert values == {0, 1, 2}
    assert not state.players[0].mana_pool


def test_pool_policy_does_not_activate_lands():
    from game.ai.mana_solver import PoolManaSolver
    from game.enums import ManaType
    state = board()
    add(state, "Island")
    add(state, "Unsummon", Z.HAND)
    add(state, "Impassioned Orator", player=1)
    generator = CandidateGenerator(mana_solver=PoolManaSolver())
    assert len(generator.generate(state, state.players[0], set())) == 1
    state.players[0].mana_pool.add_pair(ManaType.BLUE, 1)
    assert len(generator.generate(state, state.players[0], set())) > 1
