"""Contextual choices retain rule meaning without sharing hidden engine data."""
from dataclasses import replace

import pytest

from benchmarks.search_hidden_worlds import public_position, catalog
from benchmarks.search_tactics import tactical_policy
from game.ai.modular_agent import ModularAgent
from game.ai.search_actions import PlayerActionBuilder, UnsupportedPlayerAction
from game.ai.search_simulator import SearchSimulator
from game.ai.prefix_replay_simulator import PrefixReplaySimulator
from game.ai.checkpoint_simulator import CheckpointSimulator
from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.observation import PlayerObservationBuilder
from game.enums import ZoneType as Z, TurnPhase
from game.game_state import Card
from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
from game.game_actions.resolution.event_bus import GameEvent, capture_event, capture_single_card
from game.game_actions.resolution.replacement_effects import replace_zone, prevent_damage
from game.operations.card_operations import MoveCardOperation, DamagePlayerOperation


def add(game, name, zone=Z.BATTLEFIELD, owner=0):
    player = game.state.players[owner]
    card = Card(catalog()[name], player)
    player.add_card(card, zone)
    return card


def simulate(factory, backend=SearchSimulator):
    return backend(factory, policy_for=tactical_policy, track_information=True)


def pick(node, kind):
    return next(a for a in node.player_information().actions if a.kind == kind)


def resolve(game, operations, context):
    game.loop.processor.executor.resolve(game.state,
        ScheduledResolution(FixedExecutionPlan(operations), context))


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator, CheckpointSimulator])
def test_simultaneous_token_entries_have_distinct_stable_choices_before_registration(backend):
    from game.rules.permanents import CreateTokenOperation
    from game.game_state.card import CardDefinition
    from game.enums import CardType
    token = CardDefinition("Test token", types=frozenset({CardType.CREATURE}), power=1, toughness=1)
    def factory(controllers):
        game = public_position(controllers)
        context = ResolutionContext(controller=game.state.players[0])
        resolve(game, [CreateTokenOperation(context, token, 2)], context)
        return game
    sim = simulate(factory, backend)
    root = sim.start()
    assert root.decision.kind == "ReplacementOrderRequest"
    assert len(root.player_information().actions) == 2
    operations = root.observation(0)["decision_context"]["operations"]
    assert [op["card"]["pending_token"] for op in operations] == [0, 1]
    for action in root.player_information().actions:
        child = sim.advance_action(root, action)
        tokens = [c for c in child.observation(0)["battlefield"] if c["name"] == "Test token"]
        assert len(tokens) == 2 and len({c["id"] for c in tokens}) == 2
        assert sim.fork(child) == child
    assert simulate(factory, backend).start().player_information() == root.player_information()


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator, CheckpointSimulator])
def test_ceasing_public_tokens_has_visible_ordering_and_replay_identity(backend):
    from game.game_state.card import CardDefinition
    from game.enums import CardType
    token = CardDefinition("Vanishing token", types=frozenset({CardType.CREATURE}), power=1, toughness=1)
    def factory(controllers):
        game = public_position(controllers)
        for i in range(2):
            card = Card(token, game.state.players[0], key=f"vanishing-{i}")
            card.is_token = True
            card.owner.add_card(card, Z.GRAVEYARD)
        game.loop.processor.executor.settle(game.state)
        return game
    sim = simulate(factory, backend)
    root = sim.start()
    assert root.decision.kind == "ReplacementOrderRequest"
    operations = root.observation(0)["decision_context"]["operations"]
    assert [op["kind"] for op in operations] == ["CeaseTokenOperation"] * 2
    assert len(root.player_information().actions) == 2
    for action in root.player_information().actions:
        child = sim.advance_action(root, action)
        assert sim.fork(child) == child
        assert all(card["name"] != "Vanishing token" for card in child.observation(0)["graveyard"])


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_etb_trigger_orders_and_draws_keep_hidden_library_private(backend):
    before_draw = []
    for top in ("Plains", "Shock"):
        def factory(controllers):
            game = public_position(controllers)
            for _ in range(3):
                add(game, "Island")
            add(game, "Impassioned Orator")
            add(game, "Cloudkin Seer", Z.HAND)
            add(game, top, Z.DECK)
            return game
        sim = simulate(factory, backend)
        node = sim.start()
        node = sim.advance_action(node, pick(node, "AbilityAction"))
        for _ in range(2):
            node = sim.advance_action(node, pick(node, "PassPriorityAction"))
        assert node.decision.kind == "TriggerOrderRequest"
        before_draw.append(node.player_information())
        assert len(node.player_information().actions) == 2
        branches = []
        for ordered in node.player_information().actions:
            child = sim.advance_action(node, ordered)
            assert child.decision.kind == "AbilityDecisionRequest"
            while child.decision.kind == "AbilityDecisionRequest":
                child = sim.advance_action(child, child.player_information().actions[0])
            assert sim.fork(child) == child
            branches.append(child.observation(0)["pending_abilities"])
        assert branches[0] != branches[1]
        # Resolve one ordering through the actual trigger processor and stack.
        for _ in range(15):
            if any(c["name"] == top for c in node.observation(0)["hand"]):
                break
            selected = (pick(node, "PassPriorityAction") if node.decision.kind == "PriorityDecisionRequest"
                        else node.player_information().actions[0])
            node = sim.advance_action(node, selected)
        else:
            pytest.fail("Triggered draw did not resolve")
        own = [e.describe() for e in node.recall(0).entries if e.kind == "event"]
        other = [e.describe() for e in node.recall(1).entries if e.kind == "event"]
        assert next(e for e in own if e["kind"] == "card_drawn")["name"] == top
        draw = next(e for e in other if e["kind"] == "card_drawn")
        assert draw["name"] is None and draw["card_id"] is None
        assert sim.fork(node) == node
    assert before_draw[0] == before_draw[1]


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_dies_trigger_preserves_old_incarnation_and_returns_source(backend):
    def factory(controllers):
        game = public_position(controllers)
        phoenix = add(game, "Immortal Phoenix")
        ctx = ResolutionContext(controller=phoenix.owner)
        resolve(game, [MoveCardOperation(ctx, phoenix, Z.GRAVEYARD)], ctx)
        return game
    sim = simulate(factory, backend)
    node = sim.start()
    assert node.decision.kind == "TriggerOrderRequest"
    trigger = node.observation(0)["decision_context"]["triggers"][0]
    assert trigger["last_known"]["zone"] == "BATTLEFIELD"
    grave = next(c for c in node.observation(0)["graveyard"] if c["name"] == "Immortal Phoenix")
    assert trigger["source_revision"] < grave["zone_revision"]
    for _ in range(8):
        if any(c["name"] == "Immortal Phoenix" for c in node.observation(0)["hand"]):
            break
        chosen = (pick(node, "PassPriorityAction") if node.decision.kind == "PriorityDecisionRequest"
                  else node.player_information().actions[0])
        node = sim.advance_action(node, chosen)
    else:
        pytest.fail("Dies trigger did not return Phoenix")
    assert sim.fork(node) == node


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_replacement_selection_and_optional_acceptance_bind_to_local_effect(backend):
    def factory(controllers):
        game = public_position(controllers)
        card = add(game, "Pacifism", Z.HAND)
        for key, destination in (("exile", Z.EXILE), ("bounce", Z.HAND)):
            game.state.replacement_rules.append(replace_zone(key, Z.HAND, Z.GRAVEYARD,
                                                             destination, optional=True, uses=1).bind())
        ctx = ResolutionContext(controller=card.owner, source=card)
        resolve(game, [MoveCardOperation(ctx, card, Z.GRAVEYARD)], ctx)
        return game
    sim = simulate(factory, backend)
    root = sim.start()
    assert root.decision.kind == "ReplacementRequest"
    assert len(root.player_information().actions) == 2
    for selected in root.player_information().actions:
        node = sim.advance_action(root, selected)
        assert node.decision.kind == "ReplacementAcceptanceRequest"
        context = node.observation(0)["decision_context"]
        key = context["effect"]["key"]
        assert context["operation"]["card"]["name"] == "Pacifism"
        assert context["effect"]["remaining_uses"] == 1
        accepted = next(a for a in node.player_information().actions if a.describe()["accept"])
        child = sim.advance_action(node, accepted)
        zone = "exile" if key == "exile" else "hand"
        assert any(c["name"] == "Pacifism" for c in child.observation(0)[zone])
        assert sim.fork(child) == child
        declined = next(a for a in node.player_information().actions if not a.describe()["accept"])
        sibling = sim.advance_action(node, declined)
        assert sibling.decision.kind == "ReplacementAcceptanceRequest"
        assert sibling.observation(0)["decision_context"]["effect"]["key"] != key
        assert sim.fork(node) == node


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_replacement_order_keeps_damage_semantics_and_budget_isolation(backend):
    def factory(controllers):
        game = public_position(controllers)
        player = game.state.players[0]
        game.state.replacement_rules.append(prevent_damage("shield", 2, total=True).bind())
        ctx = ResolutionContext(controller=player)
        poison = DamagePlayerOperation(ctx, player, 2)
        poison.infect = True
        game.loop.processor.executor.resolve_simultaneous(game.state,
            [DamagePlayerOperation(ctx, player, 2), poison])
        return game
    sim = simulate(factory, backend)
    root = sim.start()
    assert root.decision.kind == "ReplacementOrderRequest"
    outcomes = []
    for selected in root.player_information().actions:
        node = sim.advance_action(root, selected)
        assert node.decision.kind == "PriorityDecisionRequest"
        outcomes.append(node.observation(0)["players"][0])
        assert sim.fork(node) == node
    assert len(outcomes) == 2 and outcomes[0] != outcomes[1]
    assert sorted(p["life"] for p in outcomes) == [18, 20]
    assert sim.fork(root) == root


def builder_for(game, viewer=0):
    return PlayerActionBuilder(PriorityDecisionRequest(game.state, game.state.players[viewer]))


def test_native_combat_events_preserve_attack_and_block_relationships():
    game = public_position((ModularAgent(), ModularAgent()))
    attacker = add(game, "Tin Street Cadet")
    blocker = add(game, "Impassioned Orator", owner=1)
    attacker.controlled_since = 0
    game.state.combat.begin()
    game.state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    events = game.state.combat.declare_attackers(game.state.players[0], {attacker: game.state.players[1]})
    game.state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    events += game.state.combat.declare_blockers(game.state.players[1], {blocker: attacker})
    builder = builder_for(game)
    projected = {event.key: builder.context.event(event) for event in events}
    a, b = builder.encode(attacker), builder.encode(blocker)
    assert projected["attacker_declared"]["payload"] == {"attacker": a, "defender": {"player": 1}}
    assert projected["attackers_declared"]["payload"] == {"attackers": [a]}
    assert projected["blocker_declared"]["payload"] == {"attacker": a, "blocker": b}
    assert projected["attacker_blocked"]["payload"] == {"blockers": [b]}
    assert projected["blockers_declared"]["payload"] == {"blockers": [[b, a]]}
    assert projected["attacker_blocked"]["source"] == a
    assert projected["attacker_blocked"]["controller"] == {"player": 0}


def test_trigger_event_uses_frozen_viewer_projection_not_payload_or_later_visibility():
    projections = []
    for name in ("Pacifism", "Shock"):
        game = public_position((ModularAgent(), ModularAgent()))
        hidden = add(game, name, Z.HAND, owner=1)
        event = capture_event(game.state, GameEvent("card_drawn", hidden, hidden.owner,
                              {"card": hidden, "secret": name}, triggered_abilities=()))
        hidden.owner.add_card(hidden, Z.GRAVEYARD)
        safe = builder_for(game).context.event(event)
        assert name not in str(safe) and safe["observed"]["card_id"] is None
        projections.append(safe)
        assert builder_for(game, 1).context.event(event)["observed"]["name"] == name
        with pytest.raises(UnsupportedPlayerAction, match="frozen viewer"):
            builder_for(game).context.event(replace(event, player_events=()))
    assert projections[0] == projections[1]


def test_last_known_visibility_survives_face_down_changes_and_cache_reuse():
    game = public_position((ModularAgent(), ModularAgent()))
    card = add(game, "Pacifism", Z.EXILE, owner=1)
    public = capture_single_card(game.state, card)
    assert capture_single_card(game.state, card) is public
    card.state.face_down = True
    hidden = capture_single_card(game.state, card)
    assert hidden.face_down and hidden is not public
    card.state.face_down = False
    card.owner.add_card(card, Z.HAND)
    builder = builder_for(game)
    assert builder.context.lki(public)["name"] == "Pacifism"
    with pytest.raises(UnsupportedPlayerAction, match="hidden at capture"):
        builder.context.lki(hidden)
    view = PlayerObservationBuilder(game.state, game.state.players[0])
    assert view.describe(hidden) == {"kind": "CardLastKnownInformation", "identity_visible": False}
    event = capture_event(game.state, GameEvent("card_moved", card, card.owner,
                          {"from": "EXILE", "to": "HAND", "last_known": hidden}, triggered_abilities=()))
    assert builder.context.event(event)["observed"]["name"] is None
    assert builder_for(game, 1).context.event(event)["observed"]["name"] == "Pacifism"


def test_unknown_contexts_are_rejected_and_life_loss_identifies_target():
    game = public_position((ModularAgent(), ModularAgent()))
    player = game.state.players[0]
    context = builder_for(game).context
    with pytest.raises(UnsupportedPlayerAction, match="trigger-event adapter"):
        context.event(GameEvent("custom_secret", payload={"secret": "Shock"}))
    with pytest.raises(UnsupportedPlayerAction, match="registration"):
        context.trigger_parts(None, player, None, None, None, None, object())
    loss = context.event(GameEvent("life_lost", controller=player,
                         payload={"target_object": game.state.players[1], "amount": 3}))
    assert loss["payload"] == {"target_object": {"player": 1}, "amount": 3}
    op = DamagePlayerOperation(ResolutionContext(controller=player), player, 2)
    op.extra_secret = "Shock"
    with pytest.raises(UnsupportedPlayerAction, match="unreviewed"):
        context.operation(op)
    from game.cards.starter_support import ScryOperation
    with pytest.raises(UnsupportedPlayerAction, match="operation adapter"):
        context.operation(ScryOperation(op.context, 1))
