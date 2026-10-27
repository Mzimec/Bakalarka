"""Replay branches use normal rules while retaining no live runtime objects."""
from dataclasses import fields, is_dataclass, replace
from random import Random
import random

import pytest

from game.ai.search_simulator import SearchSimulator, starter_scenario, ReplayDiverged, SimulationBudgetExceeded
from game.ai.prefix_replay_simulator import PrefixReplaySimulator
from game.ai.search_descriptors import ChoiceDescriptor, UnsupportedDescriptor, freeze
from game.ai.modular_agent import ModularAgent
from game.ai.observation import observation
from game.console.demo_game import DemoGame
from game.cards.starter_cards import starter_catalog
from game.enums import ZoneType as Z, TurnPhase as P
from game.game_state import Card, Player, State
from game.game_loop.game_loop import GameLoop
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.event_bus import EventBus


@pytest.fixture(params=[SearchSimulator, PrefixReplaySimulator], autouse=True)
def simulator_backend(request, monkeypatch):
    # Exercise the same real-engine isolation/continuation contracts on both.
    monkeypatch.setattr(__import__(__name__, fromlist=["SearchSimulator"]), "SearchSimulator", request.param)


def position(controllers, *, spell="Pacifism", lands="Plains", count=2):
    state = State([Player([], c, idx=i) for i, c in enumerate(controllers)])
    state.turn.phase = P.PRECOMBAT_MAIN
    state.rng = Random(123)
    def add(name, zone=Z.BATTLEFIELD, owner=0):
        card = Card(starter_catalog()[name], state.players[owner])
        card.owner.add_card(card, zone)
        card.controlled_since = 0
        return card
    for _ in range(count):
        add(lands)
    add(spell, Z.HAND)
    add("Impassioned Orator")
    add("Serra Angel", owner=1)
    for i in range(2):
        for _ in range(10):
            add("Island", Z.DECK, i)
    bus = EventBus()
    loop = GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), bus)))
    return DemoGame(state, bus, loop)


def choose(node, kind, *, contains=None):
    return next(c for c in node.choices if c.kind == kind and (contains is None or contains in repr(c.data)))


def pass_priority(sim, node):
    return sim.advance(node, choose(node, "PassPriorityAction"))


def test_cast_payment_stack_resolution_and_attachment_are_isolated_between_branches():
    live = position((ModularAgent(), ModularAgent()))
    before = [observation(live.state, p) for p in live.state.players]
    rng_before = live.state.rng.getstate()
    global_rng_before = random.getstate()
    sim = SearchSimulator(position)
    root = sim.start()
    assert sim.fork(root) == root
    angel = next(c for c in root.observation(0)["battlefield"] if c["name"] == "Serra Angel")
    cast = choose(root, "AbilityAction", contains=repr(("card", angel["id"], angel["zone_revision"], 1)))
    paid = sim.advance(root, cast)
    view = paid.observation(0)
    assert len(view["pending_abilities"]) == 1
    assert all(c["tapped"] for c in view["battlefield"] if c["name"] == "Plains")
    resolved = pass_priority(sim, pass_priority(sim, paid))
    aura = next(c for c in resolved.observation(0)["battlefield"] if c["name"] == "Pacifism")
    assert aura["attached_to"]["card"] == angel["id"]
    victim = next(c for c in resolved.observation(0)["battlefield"] if c["id"] == angel["id"])
    assert "can't block" in victim["keywords"]
    assert not resolved.observation(0)["pending_abilities"]
    other_branch = pass_priority(sim, root)
    assert any(c["name"] == "Pacifism" for c in other_branch.observation(0)["hand"])
    assert all(not c["tapped"] for c in other_branch.observation(0)["battlefield"] if c["name"] == "Plains")
    assert sim.fork(paid) == paid  # Stack and all payment/knowledge data reconstructed.
    assert sim.advance(root, cast) == paid
    assert [observation(live.state, p) for p in live.state.players] == before
    assert live.state.rng.getstate() == rng_before and random.getstate() == global_rng_before
    assert live.event_bus.emitted_events == []


def test_nodes_and_action_descriptors_contain_no_runtime_references():
    sim = SearchSimulator(position)
    node = sim.start()
    def check(value):
        if is_dataclass(value):
            for f in fields(value):
                if f.name != "_owner":
                    check(getattr(value, f.name))
        elif type(value) is tuple:
            for item in value:
                check(item)
        else:
            assert value is None or type(value) in (str, int, float, bool), type(value)
    check(node)
    first = node.observation(0)
    first["players"][0]["life"] = 1
    assert node.observation(0)["players"][0]["life"] == 20


def test_same_menu_with_changed_position_is_rejected_on_replay():
    life = [20]
    def factory(controllers):
        game = position(controllers)
        game.state.players[0].health = life[0]
        return game
    sim = SearchSimulator(factory)
    node = sim.start()
    life[0] = 19
    with pytest.raises(ReplayDiverged):
        pass_priority(sim, node)
    with pytest.raises(ReplayDiverged):
        sim.fork(node)


def test_foreign_nodes_invalid_choices_and_budgets_are_explicit():
    sim = SearchSimulator(position, max_decisions=1)
    node = sim.start()
    with pytest.raises(ValueError, match="another simulator"):
        SearchSimulator(position).fork(node)
    with pytest.raises(ValueError, match="descriptor"):
        sim.advance(node, 0)
    node = pass_priority(sim, node)
    with pytest.raises(SimulationBudgetExceeded, match="Decision limit"):
        pass_priority(sim, node)
    with pytest.raises(SimulationBudgetExceeded, match="max_options"):
        SearchSimulator(position, max_options=1).start()
    with pytest.raises(UnsupportedDescriptor):
        freeze(lambda: None)


def test_concession_is_terminal_and_cannot_be_advanced():
    sim = SearchSimulator(position)
    node = sim.start()
    terminal = sim.advance(node, choose(node, "ConcedeAction"))
    assert terminal.terminal and not terminal.cutoff and terminal.decision is None
    assert terminal.observation(0)["players"][0]["has_lost"]
    assert sim.fork(terminal) == terminal
    with pytest.raises(ValueError, match="terminal"):
        sim.advance(terminal, node.choices[0])


def test_seeded_setup_and_phase_continuation_are_reproducible():
    sim = SearchSimulator(starter_scenario(seed=3), max_phases=1)
    node = sim.start()
    assert node.decision.kind == "MulliganRequest"
    for _ in range(2):
        keep = next(c for c in node.choices if ("take_mulligan", ("bool", False)) in c.data[1])
        node = sim.advance(node, keep)
    assert node.cutoff and not node.terminal  # Untap completed; phase budget exhausted.
    assert sim.fork(node) == node


def test_triggered_draw_uses_normal_stack_and_publishes_only_viewer_knowledge():
    sim = SearchSimulator(lambda cs: position(cs, spell="Cloudkin Seer", lands="Island", count=3))
    node = sim.start()
    node = sim.advance(node, choose(node, "AbilityAction"))
    for _ in range(12):
        if node.decision.kind == "PriorityDecisionRequest":
            if not node.observation(0)["pending_abilities"] and len(node.observation(0)["hand"]) == 1:
                break
            node = pass_priority(sim, node)
        else:
            assert node.decision.kind in ("TriggerOrderRequest", "AbilityDecisionRequest")
            node = sim.advance(node, node.choices[0])
    else:
        pytest.fail("ETB draw did not resolve")
    own = node.observation(0)["knowledge"]["recent_events"]
    other = node.observation(1)["knowledge"]["recent_events"]
    assert next(e for e in own if e["kind"] == "card_drawn")["name"] == "Island"
    assert next(e for e in other if e["kind"] == "card_drawn")["name"] is None
    assert sim.fork(node) == node


def test_nested_scry_resumes_remaining_resolution_and_only_actor_sees_inspected_cards():
    from game.cards.starter_support import ScryOperation
    from game.operations.card_operations import DrawCardOperation
    from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
    def factory(controllers):
        game = position(controllers)
        player = game.state.players[0]
        for name in ("Mountain", "Shock"):
            player.add_card(Card(starter_catalog()[name], player), Z.DECK)
        ctx = ResolutionContext(controller=player)
        game.loop.processor.executor.resolve(game.state,
            ScheduledResolution(FixedExecutionPlan([ScryOperation(ctx, 2), DrawCardOperation(ctx)]), ctx))
        return game
    sim = SearchSimulator(factory)
    node = sim.start()
    assert node.decision.kind == "ScryRequest"
    revealed = node.observation(0)["decision_context"]["revealed_cards"]
    assert [c["name"] for c in revealed] == ["Shock", "Mountain"]
    assert "Shock" not in str(node.observation(1))
    branches = []
    for card in revealed:
        chosen = next(c for c in node.choices if len(dict(c.data[1])["top"][1]) == 2
                      and dict(c.data[1])["top"][1][0][1] == card["id"])
        branch = sim.advance(node, chosen)
        assert branch.decision.kind == "PriorityDecisionRequest"
        assert card["name"] in [c["name"] for c in branch.observation(0)["hand"]]
        assert sim.fork(branch) == branch
        branches.append(branch)
    assert branches[0].observation(0)["hand"] != branches[1].observation(0)["hand"]


def test_nested_replacement_acceptance_has_independent_runtime_budgets():
    from game.operations.card_operations import MoveCardOperation
    from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
    from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition
    def factory(controllers):
        game = position(controllers)
        state = game.state
        player = state.players[0]
        card = next(iter(player.hand.values()))
        rule = ReplacementEffectDefinition("optional_exile",
            lambda s, e, op: isinstance(op, MoveCardOperation) and op.destination == Z.GRAVEYARD,
            lambda s, e, op: (MoveCardOperation(op.context, op.card, Z.EXILE),),
            optional=True, uses=1)
        state.replacement_rules.append(rule.bind())
        ctx = ResolutionContext(controller=player, source=card)
        game.loop.processor.executor.resolve(state,
            ScheduledResolution(FixedExecutionPlan([MoveCardOperation(ctx, card, Z.GRAVEYARD)]), ctx))
        return game
    sim = SearchSimulator(factory)
    node = sim.start()
    # Some replacement arbitration paths first request ordering/selection.
    while node.decision.kind != "ReplacementAcceptanceRequest":
        assert node.decision.kind in ("ReplacementOrderRequest", "ReplacementRequest")
        node = sim.advance(node, node.choices[0])
    outcomes = []
    for accept in (False, True):
        choice = next(c for c in node.choices if ("accept", ("bool", accept)) in c.data[1])
        child = sim.advance(node, choice)
        view = child.observation(0)
        assert any(c["name"] == "Pacifism" for c in view["exile" if accept else "graveyard"])
        assert sim.fork(child) == child
        outcomes.append(view["replacement_effects"])
    assert outcomes[0] != outcomes[1]


def test_cleanup_discard_and_combat_declarations_continue_at_correct_stage():
    def cleanup(controllers):
        game = position(controllers)
        game.state.turn.phase = P.CLEANUP
        game.state.players[0].maximum_hand_size = 0
        return game
    sim = SearchSimulator(cleanup)
    root = sim.start()
    assert root.decision.kind == "DiscardRequest"
    node = sim.advance(root, root.choices[0])
    assert node.decision.phase == "UPKEEP" and node.decision.turn == 2
    assert any(c["name"] == "Pacifism" for c in node.observation(0)["graveyard"])
    assert sim.fork(node) == node

    def blockers(controllers):
        game = position(controllers)
        state = game.state
        state.combat.begin()
        state.turn.phase = P.DECLARE_ATTACKERS
        attacker = next(c for c in state.players[0].battlefield.values() if c.name == "Impassioned Orator")
        state.combat.declare_attackers(state.players[0], {attacker: state.players[1]})
        state.turn.phase = P.DECLARE_BLOCKERS
        return game
    sim = SearchSimulator(blockers)
    root = sim.start()
    assert root.decision.kind == "DeclareBlockersRequest"
    block = next(c for c in root.choices if dict(c.data[1])["declarations"][1])
    node = sim.advance(root, block)
    assert node.observation(0)["combat"]["blockers"]
    assert sim.fork(node) == node


def test_resolution_choice_binds_cards_in_rebuilt_world():
    from game.cards.starter_support import ChooseMoveOperation
    from game.game_actions.data_structs.game_action import ResolutionContext, ScheduledResolution, FixedExecutionPlan
    def factory(controllers):
        game = position(controllers)
        player = game.state.players[0]
        player.add_card(Card(starter_catalog()["Island"], player), Z.HAND)
        ctx = ResolutionContext(controller=game.state.players[1])
        game.loop.processor.executor.resolve(game.state,
            ScheduledResolution(FixedExecutionPlan([ChooseMoveOperation(ctx, player, 1)]), ctx))
        return game
    sim = SearchSimulator(factory)
    root = sim.start()
    assert root.decision.kind == "AbilityResolutionRequest"
    outcomes = []
    for choice in root.choices:
        node = sim.advance(root, choice)
        outcomes.append([c["name"] for c in node.observation(0)["graveyard"]])
        assert sim.fork(node) == node
    assert sorted(outcomes) == [["Island"], ["Pacifism"]]


def test_setup_starting_player_and_mulligan_bottom_are_real_decision_boundaries():
    from game.console.demo_game import create_starter_demo_game
    sim = SearchSimulator(lambda cs: create_starter_demo_game(cs, seed=5, starting_player_idx=None))
    node = sim.start()
    assert node.decision.kind == "StartingPlayerRequest"
    assert node.observation(0)["setup"]
    node = sim.advance(node, node.choices[0])
    assert node.decision.kind == "MulliganRequest"
    take = next(c for c in node.choices if ("take_mulligan", ("bool", True)) in c.data[1])
    node = sim.advance(node, take)
    keep = next(c for c in node.choices if ("take_mulligan", ("bool", False)) in c.data[1])
    node = sim.advance(node, keep)
    assert node.decision.kind == "MulliganBottomRequest"
    node = sim.advance(node, node.choices[0])
    assert node.decision.kind == "MulliganRequest"
    assert sim.fork(node) == node


def test_event_description_does_not_expose_other_players_projected_draws():
    from game.ai.observation import PlayerObservationBuilder
    from game.game_actions.resolution.event_bus import GameEvent
    from game.ai.knowledge import PlayerEvent
    game = position((ModularAgent(), ModularAgent()))
    event = GameEvent("card_drawn", player_events=((1, PlayerEvent("card_drawn", 1, "DRAW", 1, 1, "secret", "Secret card")),))
    data = PlayerObservationBuilder(game.state, game.state.players[0]).describe(event)
    assert "Secret card" not in repr(data) and "secret" not in repr(data)
