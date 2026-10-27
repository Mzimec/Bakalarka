"""Native declaration roots preserve rule frames, deferred triggers and choices."""
from dataclasses import asdict, replace

import pytest

from game.ai.current_position_worlds import CurrentPositionWorlds
from game.ai.decision_maker import DecisionResult, DeclareAttackersRequest, DeclareBlockersRequest
from game.ai.knowledge import PlayerKnowledge
from game.ai.search_decision import DecisionProjector
from game.ai.search_actions import PlayerActionBuilder
from game.ai.search_recall import PlayerRecall
from game.cards.catalog import game_catalog
from game.enums import TurnPhase, ZoneType
from game.simulation.search_config import search_generation_policy
from test_current_position_worlds import position
from test_game_setup import Controller


class Capture(Controller):
    def __init__(self, origin):
        super().__init__()
        self.provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
        self.knowledge, self.recall = PlayerKnowledge(), PlayerRecall()
        self.captured, self.information = [], []

    def receive_player_event(self, event, *, game_token, player):
        self.knowledge.bind(game_token, player)
        self.knowledge.observe(event)
        self.recall = self.recall.append("event", asdict(event))

    def _decide(self, request):
        options = tuple(request.option_space(search_generation_policy(request)))
        info = DecisionProjector(request, options, self.recall).build().information
        self.information.append(info)
        if isinstance(request, (DeclareAttackersRequest, DeclareBlockersRequest)):
            factory = self.provider.prepare(request, info, self.recall, self.knowledge)
            assert factory is not None, self.provider.unsupported_reason
            selected = max(options, key=lambda option: len(option.declarations))
            action = PlayerActionBuilder(request).action(selected)
            self.captured.append((factory, info, action, request.state.combat_declaration_boundary))
        else:
            selected = super()._decide(request).value
            action = PlayerActionBuilder(request).action(selected)
        self.recall = info.recall.append("action", action.describe())
        return DecisionResult(selected)


def phase_catalog():
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    from game.game_actions.triggers.trigger_condition import EventKeyCondition
    catalog = game_catalog()
    catalog["Charmed Stray"] = replace(catalog["Charmed Stray"], triggers=frozenset({
        TriggerAbilityDefinition(key="phase-marker", condition=EventKeyCondition("phase_started"))}))
    return catalog


@pytest.mark.parametrize("blockers", [False, True])
@pytest.mark.parametrize("phase_trigger", [False, True])
def test_native_combat_root_matches_live_and_forks_with_pending_triggers(blockers, phase_trigger):
    origin, game = position()
    if phase_trigger:
        origin = replace(origin, catalog_factory=phase_catalog)
        game = origin.scenario(42)((Controller(), Controller()))
        game.state.turn.number = 9
    own = [Capture(origin), Capture(origin)]
    state = game.state
    creatures = []
    for p in state.players:
        p.controller = own[p.idx]
        card = next(c for c in p.get_cards() if c.name == "Charmed Stray")
        p.move_card(card, ZoneType.BATTLEFIELD)
        card.controlled_since = 0
        creatures.append(card)
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    if blockers:
        state.combat.declare_attackers(state.active_player, {creatures[0]: state.players[1]})
        state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    actor = own[int(blockers)]
    game.loop.step(state)
    assert not hasattr(state, "combat_declaration_boundary")
    factory, info, action, boundary = actor.captured[0]
    assert bool(boundary.pending_triggers) == phase_trigger
    sim = factory(123)
    root = sim.start()
    assert root.player_information() == info
    child = sim.advance_action(root, action)
    assert child.decision.kind == ("TriggerOrderRequest" if phase_trigger else "PriorityDecisionRequest")
    assert sim.fork(root) == root and sim.fork(child) == child
    # The actual selected declaration was applied once, and the sampled
    # declaration did not alter the already-running live match.
    assert state.combat.attackers == {creatures[0]: state.players[1]}
    assert bool(state.combat.blockers) == blockers
    assert sum(event.key == "phase_started" for event in game.bus.emitted_events) == 1
    if not blockers:
        assert child.player_information() == actor.information[1]


def test_manually_forged_declaration_without_engine_boundary_is_rejected():
    origin, game = position()
    state = game.state
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    provider = CurrentPositionWorlds(origin)
    assert provider.prepare(DeclareAttackersRequest(state, state.players[0]), None, PlayerRecall(),
                            PlayerKnowledge()) is None
    assert "Missing native combat" in provider.unsupported_reason


def test_native_scry_and_draw_keep_positions_across_current_world_samples():
    from game.ai.decision_maker import ScryOption
    from game.cards.starter_support import ScryOperation
    from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
    from game.operations.card_operations import DrawCardOperation
    from test_current_position_worlds import prepare, controllers

    origin, game = position()
    class ScryCapture(Capture):
        def decide_scry(self, request):
            return DecisionResult(ScryOption((request.candidates[1],),
                                             (request.candidates[0], request.candidates[2])))
    player = game.state.players[0]
    own = ScryCapture(origin)
    player.controller = own
    context = ResolutionContext(controller=player)
    def execute(operation):
        game.loop.processor.executor.resolve(game.state,
            ScheduledResolution(FixedExecutionPlan([operation]), context))
    viewed = tuple(reversed(tuple(player.deck.values())))[:3]
    execute(ScryOperation(context, 3))
    for after_draw in (False, True):
        if after_draw:
            execute(DrawCardOperation(context))
        factory, info, snapshot = prepare(origin, game, recall=own.recall)
        orders = set()
        for seed in range(12):
            sampled = snapshot.materialize(controllers(), seed).state
            order = tuple(c.command_id for c in reversed(tuple(sampled.players[0].deck.values())))
            orders.add(order)
            assert order[-2:] == (viewed[0].command_id, viewed[2].command_id)
            if not after_draw:
                assert order[0] == viewed[1].command_id
            assert factory(seed).start().player_information() == info
        assert len(orders) > 1


def test_native_london_mulligan_bottom_is_fixed_without_history_rejection():
    from test_current_position_worlds import prepare, controllers
    origin, _ = position()
    own = Capture(origin)
    own.mulligans = 1
    game = origin.scenario(11)((own, controllers()[1]))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    assert game.state.mulligans_taken[0] == 1
    bottom = own.bottoms[0][0]
    factory, info, snapshot = prepare(origin, game, recall=own.recall)
    for seed in range(8):
        sampled = snapshot.materialize(controllers(), seed).state
        assert next(iter(sampled.players[0].deck.values())).command_id == bottom.command_id
        assert factory(seed).start().player_information() == info
