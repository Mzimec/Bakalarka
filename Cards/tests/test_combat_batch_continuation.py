"""Native prepared damage can suspend, copy, resume without past-choice sampling."""
from dataclasses import replace

import pytest

from game.ai.decision_maker import DecisionResult, ReplacementOrderRequest
from game.ai.game_origin import GameOrigin
from game.ai.search_actions import PlayerActionBuilder
from game.ai.search_decision import DecisionProjector
from game.cards.catalog import game_catalog
from game.cards.decks import DeckList
from game.enums import TurnPhase, ZoneType
from game.game_actions.generation.decision_abstraction.order_frames import OrderNextRequest
from game.game_loop.setup import SetupConfig
from game.simulation.search_config import search_generation_policy
from test_combat_search_continuation import Capture, phase_catalog


class BatchCapture(Capture):
    def __init__(self, origin, prefix):
        super().__init__(origin)
        self.prefix = prefix
        self.batches = []

    def _decide(self, request):
        from game.game_loop.continuations import is_combat_damage_order
        match = (type(request) is ReplacementOrderRequest and self.prefix is None
            or type(request) is OrderNextRequest and request.frame.kind == "ReplacementOrderRequest"
            and len(request.frame.prefix) == self.prefix)
        if not match or not is_combat_damage_order(request):
            return super()._decide(request)
        options = tuple(request.option_space(search_generation_policy(request)))
        info = DecisionProjector(request, options, self.recall).build().information
        factory = self.provider.prepare(request, info, self.recall, self.knowledge)
        assert factory is not None, self.provider.unsupported_detail
        assert self.provider.preparation["route"] == "current"
        action = PlayerActionBuilder(request).action(options[0])
        self.batches.append((factory, info, action, request.state.combat_damage_batch_boundary))
        self.recall = info.recall.append("action", action.describe())
        return DecisionResult(options[0])


@pytest.mark.parametrize("count,prefix", [(2, None), (7, 0), (7, 3)])
@pytest.mark.parametrize("phase_trigger", [False, True])
@pytest.mark.parametrize("shield", [False, True])
def test_native_damage_batch_preserves_prefix_deferred_triggers_and_world_isolation(count, prefix, phase_trigger, shield):
    deck = DeckList("Combat frame", (("Charmed Stray", 8), ("Plains", 2)))
    origin = GameOrigin((deck, deck), phase_catalog if phase_trigger else game_catalog,
        starting_player_idx=0, config=SetupConfig(opening_hand_size=2, minimum_deck_size=10, maximum_copies=None))
    controllers = (BatchCapture(origin, prefix), BatchCapture(origin, prefix))
    game = origin.scenario(42)(controllers)
    state = game.state
    state.turn.number = 9
    attackers = [c for c in state.players[0].get_cards() if c.name == "Charmed Stray"][:count]
    for card in attackers:
        card.owner.move_card(card, ZoneType.BATTLEFIELD)
        card.controlled_since = 0
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.players[0], {card: state.players[1] for card in attackers})
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], {})
    state.turn.phase = TurnPhase.SECOND_COMBAT_DAMAGE
    if shield:
        from game.game_actions.resolution.replacement_effects import prevent_damage
        state.replacement_rules.append(prevent_damage("finite-shield", 1, total=True).bind())
    game.loop.step(state)
    assert not hasattr(state, "replacement_order_boundary")
    assert not hasattr(state, "combat_damage_batch_boundary")
    assert not hasattr(state, "order_decision_frame")
    assert state.players[1].health == 20-count+shield
    assert sum(e.key == "phase_started" for e in game.bus.emitted_events) == 1
    factory, info, action, boundary = controllers[1].batches[0]
    assert bool(boundary.pending_triggers) == phase_trigger
    assert len(boundary.operations) == count
    for seed in (1, 2):
        sim = factory(seed)
        root = sim.start()
        assert root.player_information() == info
        assert sim.fork(root) == root
        node = sim.advance_action(root, action)
        # Finish remaining microchoices and phase triggers using the same engine.
        while node.decision.kind != "PriorityDecisionRequest":
            node = sim.advance_action(node, node.player_information().actions[0])
        assert node.observation(1)["players"][1]["life"] == 20-count+shield
        assert node.observation(0)["players"][0]["life"] == 20+count-shield
        assert sim.fork(node) == node
    assert state.players[1].health == 20-count+shield


def test_resumed_nonactive_group_preserves_completed_apnap_group_and_simultaneous_deaths():
    deck = DeckList("Two-sided damage", (("Charmed Stray", 4), ("Plains", 6)))
    origin = GameOrigin((deck, deck), game_catalog, starting_player_idx=0,
        config=SetupConfig(opening_hand_size=2, minimum_deck_size=10))
    controllers = (BatchCapture(origin, None), BatchCapture(origin, None))
    game = origin.scenario(42)(controllers)
    state = game.state
    state.turn.number = 9
    groups = []
    for player in state.players:
        cards = [c for c in player.get_cards() if c.name == "Charmed Stray"][:2]
        for card in cards:
            player.move_card(card, ZoneType.BATTLEFIELD)
            card.controlled_since = 0
        groups.append(cards)
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.players[0], {card: state.players[1] for card in groups[0]})
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], dict(zip(groups[1], groups[0])))
    state.turn.phase = TurnPhase.SECOND_COMBAT_DAMAGE
    game.loop.step(state)
    factory, info, action, _ = controllers[1].batches[0]
    sim = factory(123)
    root = sim.start()
    assert root.player_information() == info
    assert root.decision.player == 1
    child = sim.advance_action(root, action)
    # The first new decision is SBA ordering, not a repeat of player 0's
    # already committed combat-damage order.
    assert child.decision.kind == "ReplacementOrderRequest"
    assert {o["kind"] for o in child.observation(child.decision.player)["decision_context"]["operations"]} == {"MoveCardOperation"}
    while child.decision.kind != "PriorityDecisionRequest":
        child = sim.advance_action(child, child.player_information().actions[0])
    assert [p["life"] for p in child.observation(0)["players"]] == [p.health for p in state.players] == [22, 22]
    assert len(child.observation(0)["graveyard"]) == 4
    assert sim.fork(root) == root and sim.fork(child) == child


def test_forged_combat_order_without_explicit_frames_stays_unsupported():
    from game.game_loop.continuations import is_combat_damage_order
    from test_current_position_worlds import position
    _, game = position()
    assert not is_combat_damage_order(ReplacementOrderRequest(game.state, game.state.players[0], ()))
