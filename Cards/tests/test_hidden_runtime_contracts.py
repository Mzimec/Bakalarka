"""Contract regressions, independent of the original failing match histories."""
import ast
from dataclasses import replace
from itertools import permutations
from pathlib import Path

import pytest

from game.ai.decision_maker import PriorityDecisionRequest, ModularDecisionMaker
from game.ai.search_actions import PlayerActionBuilder
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.runtime_clone import RuntimeClone
from game.game_actions.resolution.event_bus import GameEvent, capture_player_events
from game.game_actions.resolution.event_definitions import EVENT_DEFINITIONS
from game.game_actions.generation.decision_abstraction.auxiliary import ReplacementOrderRequest
from game.game_actions.generation.decision_abstraction.order_frames import OrderDecisionFrame, OrderNextRequest
from game.simulation.search_config import search_generation_policy
from game.enums import ZoneType, TurnPhase

from test_search_context import public_position, add


def test_every_literal_native_event_has_an_explicit_schema():
    root = Path(__file__).resolve().parents[1] / "game"
    seen = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "GameEvent":
                value = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "key"), None)
                if isinstance(value, ast.Constant) and type(value.value) is str:
                    seen.add(value.value)
    assert seen <= EVENT_DEFINITIONS.keys()
    assert {"spell_cast", "ability_activated", "library_shuffled"} <= EVENT_DEFINITIONS.keys()


@pytest.mark.parametrize("key", ["spell_cast", "ability_activated"])
def test_cast_and_activation_contexts_expose_reviewed_definition_key_only(key):
    game = public_position((ModularDecisionMaker(), ModularDecisionMaker()))
    card = add(game, "Cloudkin Seer", ZoneType.STACK)
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    ability = next(iter(card.definition.abilities))
    event = GameEvent(key, card, card.owner, {"ability": ability, "unreviewed": object()})
    context = PlayerActionBuilder(request).context.event(event)
    assert context["payload"] == {"ability": ability.key}


def test_capture_freezes_both_viewers_even_without_controller_hooks():
    game = public_position((ModularDecisionMaker(), ModularDecisionMaker()))
    card = add(game, "Shock", ZoneType.HAND, owner=1)
    event = capture_player_events(game.state, GameEvent("card_drawn", card, card.owner))
    projections = dict(event.player_events)
    assert set(projections) == {0, 1}
    assert projections[0].name is None and projections[0].card_id is None
    assert projections[1].name == "Shock"
    card.owner.move_card(card, ZoneType.GRAVEYARD)
    for player in game.state.players:
        request = PriorityDecisionRequest(game.state, player)
        observed = PlayerActionBuilder(request).context.event(event)["observed"]
        assert observed["name"] == ("Shock" if player.idx == 1 else None)


def test_knowledge_identity_is_separate_from_offered_candidate_identity():
    game = public_position((ModularDecisionMaker(), ModularDecisionMaker()))
    add(game, "Forest", ZoneType.HAND)
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    options = tuple(request.option_space(search_generation_policy(request)))
    full = DecisionProjector(request, options, PlayerRecall()).build().information
    reduced = DecisionProjector(request, options[:1], PlayerRecall()).build().information
    assert full.knowledge_key == reduced.knowledge_key
    assert full.recall == reduced.recall
    assert full.menu_digest != reduced.menu_digest
    assert full.key != reduced.key
    assert full.recall.entries[-1].describe()["contract_version"] == 2


def test_hidden_effect_cannot_toggle_priority_source_reduction():
    game = public_position((ModularDecisionMaker(), ModularDecisionMaker()))
    add(game, "Forest", ZoneType.HAND)
    add(game, "Forest", ZoneType.HAND)
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    def menu():
        return DecisionProjector(request, tuple(request.option_space(search_generation_policy(request))), PlayerRecall()).build().information
    before = menu()
    # This private source has no effect on the acting player's legal actions.
    add(game, "Winged Words", ZoneType.HAND, owner=1)
    game.state.refresh_continuous_effects()
    after = menu()
    assert before.actions == after.actions
    assert sum(a.kind == "LandPlayAction" for a in after.actions) == 2


def test_small_prefix_space_equals_exhaustive_orders_and_clone_is_isolated():
    candidates = tuple(object() for _ in range(4))
    frame = OrderDecisionFrame("ReplacementOrderRequest", candidates)
    def leaves(frame):
        if not frame.remaining:
            yield frame.finish().items
        for index in frame.remaining:
            yield from leaves(frame.advance(index))
    assert set(leaves(frame)) == set(permutations(candidates))
    child = frame.advance(2)
    copied = RuntimeClone().copy(child)
    assert copied.prefix == (2,) and len(copied.remaining) == 3
    assert frame.prefix == ()
    with pytest.raises(ValueError):
        child.advance(2)
    with pytest.raises(ValueError):
        child.finish()


@pytest.mark.parametrize("count", [7, 11])
def test_large_live_order_exposes_linear_menus_and_keeps_runtime_unmodified(count):
    from game.ai.decision_maker import DecisionResult
    class PickLast(ModularDecisionMaker):
        def __init__(self):
            self.sizes = []
        def decide_order_next(self, request):
            assert type(request) is OrderNextRequest
            self.sizes.append(len(tuple(request.options)))
            return DecisionResult(tuple(request.options)[-1])
    chooser = PickLast()
    game = public_position((chooser, ModularDecisionMaker()))
    before = game.state.players[0].get_life(game.state) if hasattr(game.state.players[0], "get_life") else game.state.players[0].health
    candidates = tuple(object() for _ in range(count))
    result = chooser.decide(ReplacementOrderRequest(game.state, game.state.players[0], candidates))
    assert result.value.items == tuple(reversed(candidates))
    assert chooser.sizes == list(range(count, 0, -1))
    assert game.state.players[0].health == before


@pytest.mark.parametrize("count", [7, 11])
def test_large_simulated_order_is_forkable_and_no_damage_executes_before_completion(count):
    from game.ai.search_simulator import SearchSimulator
    from game.game_actions.data_structs.game_action import ResolutionContext
    from game.operations.card_operations import DamagePlayerOperation
    from game.game_actions.resolution.replacement_effects import ReplacementResolver
    from game.game_actions.resolution.operation_executor import OperationExecutor
    def factory(controllers):
        game = public_position(controllers)
        player = game.state.players[0]
        context = ResolutionContext(controller=player)
        operations = [DamagePlayerOperation(context, player, 1) for _ in range(count)]
        ordered = ReplacementResolver().replace(game.state, operations)
        OperationExecutor().execute_batch(game.state, ordered)
        return game
    sim = SearchSimulator(factory, max_options=12, track_information=True)
    node = sim.start()
    for step in range(count):
        assert node.decision.kind == "OrderNextRequest"
        assert len(node.choices) == count-step
        assert node.observation(0)["players"][0]["life"] == 20
        assert sim.fork(node) == node
        node = sim.advance_action(node, node.player_information().actions[0])
    assert node.observation(0)["players"][0]["life"] == 20-count


def test_public_dynamic_cost_effect_rebuilds_private_targets_after_sampling():
    from game.ai.game_origin import GameOrigin
    from game.cards.decks import DeckList
    from game.cards.catalog import game_catalog
    from game.game_loop.setup import SetupConfig
    from test_current_position_worlds import controllers, prepare
    deck = DeckList("Warden regression", (("Island", 6), ("Warden of Evos Isle", 2), ("Cloudkin Seer", 2)))
    origin = GameOrigin((deck, deck), game_catalog, starting_player_idx=0,
        config=SetupConfig(opening_hand_size=2, minimum_deck_size=10))
    game = origin.scenario(42)(controllers())
    state = game.state
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    other = state.players[1]
    other.move_card(next(c for c in other.get_cards() if c.name == "Warden of Evos Isle"), ZoneType.BATTLEFIELD)
    other.move_card(next(c for c in other.get_cards() if c.name == "Cloudkin Seer"), ZoneType.HAND)
    state.synchronise_registers()
    original = [(e.key, e.state.timestamp_order, set(e.state.currently_affected)) for e in state._cont_effect_manager.query()]
    factory, info, snapshot = prepare(origin, game)
    seen_hands = set()
    for seed in range(12):
        sample = snapshot.materialize(controllers(), seed).state
        assert factory(seed).start().player_information() == info
        effect = next(e for e in sample._cont_effect_manager.query() if e.definition.static_ability_key == "warden_reduction")
        assert effect.state.currently_affected == set(effect.get_affected(sample))
        assert all(c._game_state is sample for c in effect.state.currently_affected)
        for card in effect.state.currently_affected:
            assert card.get_casting_cost(sample).generic == card.get_mana_cost(sample).generic-1
        seen_hands.add(tuple(c.name for c in sample.players[1].hand.values()))
    assert len(seen_hands) > 1
    assert [(e.key, e.state.timestamp_order, set(e.state.currently_affected)) for e in state._cont_effect_manager.query()] == original


def test_observed_effect_collection_does_not_depend_on_register_slot_order():
    from game.ai.observation import PlayerObservationBuilder
    from game.game_state.registers.effect_register import EffectRegister
    game = public_position((ModularDecisionMaker(), ModularDecisionMaker()))
    add(game, "Warden of Evos Isle")
    add(game, "Windstorm Drake")
    state = game.state
    state.synchronise_registers()
    before = PlayerObservationBuilder(state, state.players[0]).build(update_knowledge=False)
    manager = state._cont_effect_manager
    effects = tuple(manager.query())
    assert len(effects) >= 2
    manager.register = EffectRegister()
    for effect in reversed(effects):
        manager.register.add(effect)
    assert tuple(manager.query()) == tuple(reversed(effects))
    after = PlayerObservationBuilder(state, state.players[0]).build(update_knowledge=False)
    assert before == after


def test_dynamic_projection_does_not_erase_extension_owned_rule_state():
    from game.game_state.modifier import ContinuousEffectState
    from game.ai.snapshot_effects import project_hidden_static_effects
    game = public_position((ModularDecisionMaker(), ModularDecisionMaker()))
    add(game, "Warden of Evos Isle")
    state = game.state
    state.synchronise_registers()
    effect = state._cont_effect_manager.query()[0]
    effect.state.remaining_uses = 3  # Not part of the reviewed native cache.
    projected, _ = project_hidden_static_effects(state, set(state.get_cards()))
    assert projected is None
    assert effect.state.remaining_uses == 3


def test_fixed_work_search_recommendation_ignores_actual_hidden_static_membership():
    from test_snapshot_effects import position, prepare
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.observation_policy import ObservationOpponentPolicy
    origin, game = position()
    _, first, info = prepare(origin, game)
    other = game.state.players[1]
    winged = next(c for c in other.hand.values() if c.name == "Winged Words")
    island = next(c for c in other.deck.values() if c.name == "Island")
    other.hand.pop(winged.key)
    other.deck.pop(island.key)
    winged.state.zone_info.zone = ZoneType.DECK
    island.state.zone_info.zone = ZoneType.HAND
    other.deck.append(winged)
    other.hand.append(island)
    game.state.card_register.mark_changed(winged)
    game.state.card_register.mark_changed(island)
    _, second, after = prepare(origin, game)
    assert info == after
    policy = ObservationOpponentPolicy()
    def run(factory):
        return InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
            require_root_coverage=False,
            budget=InformationSearchBudget(worlds=2, iterations=8, max_depth=2, max_advances=256)).search(
                factory, info, seed=111, fallback=policy(info))
    before, after = run(first), run(second)
    assert not before.used_fallback and not after.used_fallback
    assert before.choice == after.choice and before.value == after.value
    assert before.estimates == after.estimates
    assert before.metrics.advance_attempts == after.metrics.advance_attempts
    assert before.metrics.iterations_completed == after.metrics.iterations_completed
    assert before.metrics.interruption_reasons == after.metrics.interruption_reasons
