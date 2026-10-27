"""Current-root sampling, native continuation, visibility and graph isolation."""
from collections import Counter
from types import MappingProxyType

import pytest

from game.ai.current_position_worlds import CurrentPositionWorlds, _Snapshot
from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.game_origin import GameOrigin
from game.ai.knowledge import PlayerKnowledge
from game.ai.runtime_clone import RuntimeClone
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.cards.catalog import game_catalog
from game.cards.decks import DeckList
from game.enums import TurnPhase, ZoneType, CounterType
from game.game_loop.setup import SetupConfig
from game.simulation.search_config import search_generation_policy
from test_game_setup import Controller


def controllers():
    result = (Controller(), Controller())
    for c in result:
        c.knowledge = PlayerKnowledge()
    return result


def position():
    deck = DeckList("Copies", (("Mountain", 5), ("Shock", 2), ("Pacifism", 1),
                               ("Angel of Vitality", 1), ("Charmed Stray", 1)))
    origin = GameOrigin((deck, deck), game_catalog, starting_player_idx=0,
                        config=SetupConfig(opening_hand_size=2, minimum_deck_size=10))
    game = origin.scenario(42)(controllers())
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    game.state.turn.number = 9
    return origin, game


def prepare(origin, game, player=0, recall=None):
    state = game.state
    state.priority.current_player = state.players[player]
    request = PriorityDecisionRequest(state, state.players[player])
    prior = recall or PlayerRecall()
    options = tuple(request.option_space(search_generation_policy(request)))
    info = DecisionProjector(request, options, prior).build().information
    knowledge = state.players[player].controller.knowledge
    provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
    factory = provider.prepare(request, info, prior, knowledge)
    assert factory is not None, provider.unsupported_reason
    return factory, info, _Snapshot(request, info, prior, knowledge, origin)


def test_late_root_needs_no_past_replay_and_keeps_full_private_recall():
    origin, game = position()
    prior = PlayerRecall().append("event", {"kind": "old_visible_evidence"})
    factory, info, _ = prepare(origin, game, recall=prior)
    for seed in (1, 2, 3):
        sim = factory(seed)
        root = sim.start()
        assert root.player_information() == info
        assert root.history == () and root.phase_steps == 0
        assert root.decision.turn == 9
        assert sim.fork(root) == root


def test_derived_trigger_cache_cannot_retain_inaccessible_private_cards():
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    from game.game_actions.triggers.trigger_condition import EventKeyCondition
    from game.game_actions.triggers.trigger_roster import TriggerBinding
    from game.game_actions.triggers.trigger_registry import TriggerRoster
    origin, game = position()
    state = game.state
    hidden = next(iter(state.players[1].hand.values()))
    definition = TriggerAbilityDefinition(key="cached", condition=EventKeyCondition("test"))
    binding = TriggerBinding(definition, hidden, hidden.owner)
    # A read cache is not authoritative rules state. It must not force the
    # filtered snapshot copier to inspect an opponent's real private identity.
    state._trigger_registry._bindings[hidden.key] = (binding,)
    state._trigger_registry._roster = TriggerRoster((binding,))
    factory, information, snapshot = prepare(origin, game)
    sampled = snapshot.materialize(controllers(), 123).state
    assert not sampled._trigger_registry._bindings
    assert factory(123).start().player_information() == information


def test_sampled_hands_vary_without_changing_root_and_preserve_declared_copies():
    origin, game = position()
    factory, info, snapshot = prepare(origin, game)
    hands = set()
    for seed in range(20):
        sampled = snapshot.materialize(controllers(), seed).state
        hands.add(tuple(c.name for c in sampled.players[1].hand.values()))
        for p in sampled.players:
            assert Counter(c.name for c in p.get_cards()) == dict(origin.decks[p.idx].cards)
        assert factory(seed).start().player_information() == info
    assert len(hands) > 1


def test_real_opponent_hand_library_and_rng_cannot_change_a_sample():
    origin, game = position()
    factory, info, first = prepare(origin, game)
    state = game.state
    opponent = state.players[1]
    hand, deck = tuple(opponent.hand.values()), tuple(opponent.deck.values())
    # Swap actual private membership and reverse the library, keeping only
    # publicly known sizes fixed. No event: this is the inaccessible counterfactual.
    opponent.hand.clear()
    opponent.deck.clear()
    for card in deck[:len(hand)]:
        card.state.zone_info.zone = ZoneType.HAND
        opponent.hand.append(card)
    for card in reversed((*hand, *deck[len(hand):])):
        card.state.zone_info.zone = ZoneType.DECK
        opponent.deck.append(card)
    state.rng.seed(9999)
    _, other_info, second = prepare(origin, game)
    assert other_info == info
    a, b = (s.materialize(controllers(), 123).state for s in (first, second))
    assert [(tuple(p.hand), tuple(p.deck)) for p in a.players] == [(tuple(p.hand), tuple(p.deck)) for p in b.players]
    assert a.rng.getstate() == b.rng.getstate()


def test_known_opponent_hand_copy_is_pinned_and_not_read_from_live_private_memory():
    origin, game = position()
    observer, opponent = game.state.players
    card = next(iter(opponent.hand.values()))
    knowledge = observer.controller.knowledge
    knowledge.bind(game.state.knowledge_token, 0)
    knowledge._remember(card.command_id, card.name, 1, "HAND", "event")
    _, _, snapshot = prepare(origin, game)
    for seed in range(10):
        sampled = snapshot.materialize(controllers(), seed).state
        assert card.key in sampled.players[1].hand
        assert sampled.players[1].controller.knowledge.events == []


def test_closure_readonly_mapping_and_bound_callbacks_are_independent():
    data = {"value": [1]}
    def callback():
        data["value"].append(2)
    cloned = RuntimeClone().copy((MappingProxyType(data), callback))
    cloned[1]()
    assert cloned[0]["value"] == [1, 2] and data["value"] == [1]
    result = RuntimeClone().copy((data["value"], data["value"].append))
    result[1](3)
    assert result[0] == [1, 3] and data["value"] == [1]


def test_stack_root_rebinds_spell_and_future_branches_without_live_changes():
    origin, game = position()
    player = game.state.players[0]
    mountain = next(c for c in player.get_cards() if c.name == "Mountain")
    shock = next(c for c in player.get_cards() if c.name == "Shock")
    player.move_card(mountain, ZoneType.BATTLEFIELD)
    player.move_card(shock, ZoneType.HAND)
    request = PriorityDecisionRequest(game.state, player)
    cast = next(a for a in request.option_space(search_generation_policy(request))
                if getattr(a, "source", None) is shock)
    game.loop.processor.process(game.state, cast)
    assert not game.state.stack.is_empty()
    factory, info, snapshot = prepare(origin, game)
    sim = factory(7)
    root = sim.start()
    assert root.player_information() == info
    child = sim.advance_action(root, next(a for a in info.actions if a.kind == "PassPriorityAction"))
    assert child.decision.player == 1 and child.decision.phase == "PRECOMBAT_MAIN"
    assert sim.fork(child) == child
    assert not game.state.stack.is_empty() and shock.get_zone() == ZoneType.STACK


def test_known_library_order_is_not_silently_forgotten_and_shuffle_clears_it():
    origin, game = position()
    prior = PlayerRecall().append("observation", {"request_kind": "ScryRequest"})
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    provider = CurrentPositionWorlds(origin)
    assert provider.prepare(request, None, prior, game.state.players[0].controller.knowledge) is None
    assert "library ordering" in provider.unsupported_reason
    prior = prior.append("event", {"kind": "library_shuffled", "owner": 0})
    factory, info, _ = prepare(origin, game, recall=prior)
    assert factory(1).start().player_information() == info


def test_attachments_counters_static_timestamps_indexes_and_rng_are_isolated():
    origin, game = position()
    state = game.state
    cards = {c.name: c for c in state.players[0].get_cards()}
    angel, aura, host = (cards[n] for n in ("Angel of Vitality", "Pacifism", "Charmed Stray"))
    for c in (angel, aura, host):
        c.owner.move_card(c, ZoneType.BATTLEFIELD)
    aura.attach(host, state)
    host.state.counters[CounterType.PLUS_ONE] = 1
    state.synchronise_registers()
    state.turn.number += 1  # The static effect's creation time must stay older.
    factory, info, snapshot = prepare(origin, game)
    assert factory(1).start().player_information() == info
    a, b = (snapshot.materialize(controllers(), 1).state for _ in range(2))
    find = lambda s, c: s.card_register.get_by_key(c.key)
    assert find(a, aura).state.attach_info.attached_to is find(a, host)
    assert find(a, host).state._on_change.__self__ is find(a, host)
    before = state.rng.getstate()
    find(a, aura).detach()
    find(a, host).state.counters.clear()
    a.rng.random()
    assert find(b, aura).state.attach_info.attached_to is find(b, host)
    assert find(b, host).state.counters[CounterType.PLUS_ONE] == 1
    assert host.state.counters[CounterType.PLUS_ONE] == 1
    assert aura.state.attach_info.attached_to is host
    assert state.rng.getstate() == before
    assert all(c._game_state is a for c in a.get_cards())


def test_resume_preserves_prior_pass_and_finishes_phase_once():
    origin, game = position()
    game.state.priority.passed_players = {game.state.players[0]}
    factory, info, _ = prepare(origin, game, player=1)
    sim = factory(3)
    root = sim.start()
    assert root.player_information() == info
    action = next(a for a in info.actions if a.kind == "PassPriorityAction")
    child = sim.advance_action(root, action)
    assert child.decision.phase == "BEGIN_COMBAT"
    assert child.decision.player == 0
    assert sim.fork(child) == child


@pytest.mark.parametrize("phase", [TurnPhase.UNTAP, TurnPhase.CLEANUP])
def test_special_phase_continuations_are_explicitly_unsupported(phase):
    origin, game = position()
    game.state.turn.phase = phase
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    provider = CurrentPositionWorlds(origin)
    assert provider.prepare(request, None, None, None) is None
    assert provider.unsupported_reason == "unsupported_phase_continuation"
