"""Information available to a player, including public relationships and rules."""
from dataclasses import replace
from types import SimpleNamespace
import json

from game.ai.observation import observation
from game.ai.modular_agent import ModularAgent, Candidate
from game.ai.llm_policy import OllamaSelector
from game.game_state import State, Player, Card, CardDefinition
from game.enums import ZoneType as Z, CardType, CounterType, TurnPhase, SAVariableType
from game.stat_type import STAT_POWER, STAT_KEYWORDS
from game.game_state.modifier import AddIntModifier, AddSetModifier


def board():
    return State([Player([], ModularAgent(), idx=i) for i in range(2)])


def add(state, name, zone=Z.BATTLEFIELD, player=0, **kwargs):
    definition = CardDefinition(name, **kwargs)
    card = Card(definition, state.players[player])
    card.owner.add_card(card, zone)
    return card


def find(view, card, zone="battlefield"):
    return next(row for row in view[zone] if row["id"] == card.command_id)


def test_attachment_links_evaluated_keywords_counters_damage_and_control():
    state = board()
    host = add(state, "Host", types=frozenset({CardType.CREATURE}), power=2, toughness=3)
    aura = add(state, "Aura", types=frozenset({CardType.ENCHANTMENT}),
               attach_mods={STAT_POWER: (AddIntModifier(3),),
                            STAT_KEYWORDS: (AddSetModifier(frozenset({"flying"})),)})
    aura.attach(host, state)
    host.state.counters[CounterType.PLUS_ONE] = 1
    host.state.damage_marked = 2
    host.state.damage_by_deathtouch = True
    host.set_controller(state.players[1])
    view = observation(state, state.players[0])
    row = find(view, host)
    assert row["owner"] == 0 and row["controller"] == 1
    assert (row["power"], row["toughness"]) == (6, 4)
    assert row["keywords"] == ["flying"]
    assert row["damage_marked"] == 2 and row["damage_by_deathtouch"]
    assert row["counters"]["PLUS_ONE"] == 1
    assert row["attachments"][0]["card"] == aura.command_id
    assert find(view, aura)["attached_to"]["card"] == host.command_id
    aura.detach()
    host.state.counters.clear()
    current = observation(state, state.players[0])
    assert find(current, host)["power"] == 2
    assert not find(current, host)["attachments"] and find(current, aura)["attached_to"] is None
    assert row["power"] == 6 and row["counters"]["PLUS_ONE"] == 1


def test_visibility_public_zones_and_face_down_cards():
    state = board()
    add(state, "Own hand", Z.HAND)
    add(state, "Opponent secret", Z.HAND, 1)
    add(state, "Own library secret", Z.DECK)
    add(state, "Opponent library secret", Z.DECK, 1)
    grave = add(state, "Public grave", Z.GRAVEYARD, 1)
    exile = add(state, "Public exile", Z.EXILE, 1)
    hidden = add(state, "Face down secret", Z.EXILE, 1, oracle_text="Secret rules text")
    hidden.state.face_down = True
    view = observation(state, state.players[0])
    text = json.dumps(view)
    for secret in ("Opponent secret", "Own library secret", "Opponent library secret",
                   "Face down secret", "Secret rules text"):
        assert secret not in text
    assert find(view, grave, "graveyard")["name"] == "Public grave"
    assert find(view, exile, "exile")["name"] == "Public exile"
    assert not find(view, hidden, "exile")["identity_visible"]
    assert view["players"][1]["hand_size"] == 1
    assert view["players"][0]["library_size"] == 1


def test_stack_keeps_order_targets_multiplicities_and_parameters():
    from game.game_actions.data_structs.game_action import ResolutionContext
    from game.game_actions.data_structs.ability import EffectSequence, EffectBinding
    from game.game_actions.card_effects import DamagePlayerEffect
    from game.abilities.parameter_context import ParameterContext
    state = board()
    source = add(state, "Source", Z.STACK, oracle_text="Deal damage.")
    target = add(state, "Target", player=1)
    hidden = add(state, "SECRET library", Z.DECK, 1)
    context = ResolutionContext(
        source=source, controller=state.players[0], action_key="damage",
        source_revision=source.zone_revision,
        targets={"targets": {"first": {target: 1, state.players[1]: 2, hidden: 1}}},
        param_ctx=ParameterContext({SAVariableType.X: 3}),
        effects=EffectSequence((EffectBinding(DamagePlayerEffect("damage", 3, "targets"), frozenset()),)),
    )
    for ctx in (context, replace(context, action_key="response")):
        state.stack.items.append(SimpleNamespace(action_resolution=SimpleNamespace(context=ctx)))
    view = observation(state, state.players[0])
    bottom, top = view["pending_abilities"]
    assert bottom["ability"] == "damage" and not bottom["resolves_next"]
    assert top["ability"] == "response" and top["resolves_next"]
    values = bottom["targets"]["targets"]["first"]
    assert values[0]["object"]["card"] == target.command_id
    assert values[1] == {"object": {"player": 1}, "value": 2}
    assert values[2]["object"] is None
    assert bottom["parameters"]["x_variables"]["X"] == 3
    assert "Deal 3 damage" in bottom["effects"]["sequence"][0]["effect"]["text"]
    assert "SECRET library" not in json.dumps(view)


def test_combat_remembers_blocked_attacker_after_blocker_leaves():
    state = board()
    attacker = add(state, "Attacker", types=frozenset({CardType.CREATURE}), power=2, toughness=2,
                   keywords=frozenset({"haste"}))
    blocker = add(state, "Blocker", player=1, types=frozenset({CardType.CREATURE}), power=1, toughness=1)
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.players[0], {attacker: state.players[1]})
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], {blocker: attacker})
    blocker.owner.move_card(blocker, Z.GRAVEYARD, state)
    combat = observation(state, state.players[0])["combat"]
    assert combat["defenders"][attacker.command_id] == {"player": 1}
    assert attacker.command_id in combat["blocked"]
    assert not combat["blockers"]


def test_player_resources_and_human_readable_mana():
    state = board()
    card = add(state, "Spell", Z.HAND, mana_cost="{2}{U}")
    player = state.players[0]
    player.poison_counters = 4
    player.lands_played_this_turn = 1
    player.land_plays_per_turn = 2
    state.priority.passed_players.add(state.players[1])
    view = observation(state, player)
    assert find(view, card, "hand")["cost"] == "{2}{U}"
    assert view["players"][0]["poison_counters"] == 4
    assert view["players"][0]["land_plays_per_turn"] == 2
    assert view["players"][0]["lands_played_this_turn"] == 1
    assert view["passed_players"] == [1] and view["priority"] == 0


def test_continuous_duration_and_affected_objects_are_visible():
    from game.game_state.continuous_rules import StaticContinuousRule
    from game.target.target_spec import QueryTargetSpec
    from helper.query_system.query import EqQuery
    from game.game_state.registers.card_register import IK_KEY
    state = board()
    host = add(state, "Host", types=frozenset({CardType.CREATURE}), power=2, toughness=3)
    rule = StaticContinuousRule("bonus", QueryTargetSpec(EqQuery(IK_KEY, host.key)),
                                {STAT_POWER: (AddIntModifier(4),)})
    source = add(state, "Anthem", types=frozenset({CardType.ENCHANTMENT}), continuous_effects=(rule,))
    view = observation(state, state.players[0])
    effect = next(e for e in view["continuous_effects"] if e["source"]["card"] == source.command_id)
    assert effect["affected"][0]["card"] == host.command_id
    assert effect["modifiers"]["power"][0]["value"] == 4
    assert effect["duration"] is not None
    assert find(view, host)["power"] == 6


def test_llm_gets_rich_view_without_heuristic_priors_by_default():
    state = board()
    add(state, "Visible", Z.GRAVEYARD)
    payloads = []
    selector = OllamaSelector("fake", transport=lambda payload: payloads.append(payload) or '{"choice": 1}')
    view = observation(state, state.players[0])
    assert selector.choose(view, [Candidate(None, 100, {"kind": "pass"}), Candidate(None, -100, {})]) == 1
    body = json.loads(payloads[0]["messages"][1]["content"])
    assert body["state"] == view and body["state"]["schema_version"] == 2
    assert all("prior" not in candidate for candidate in body["candidates"])
    selector.include_priors = True
    selector.choose(view, [Candidate(None, 100, {}), Candidate(None, -100, {})])
    assert json.loads(payloads[1]["messages"][1]["content"])["candidates"][0]["prior"] == 100


def test_static_replacement_budget_is_reported_without_resetting_it():
    from game.game_actions.resolution.replacement_effects import prevent_damage
    state = board()
    definition = prevent_damage("shield", 4)
    source = add(state, "Shield", replacement_effects=(definition,))
    bound = definition.bind(source)
    bound.remaining_budget = 1
    state._replacement_instances = {(id(source), source.zone_revision, 0): bound}
    instances = state._replacement_instances
    view = observation(state, state.players[0])
    assert view["replacement_effects"][0]["remaining_budget"] == 1
    assert state._replacement_instances is instances
    assert bound.remaining_budget == 1


def test_current_granted_abilities_are_not_replaced_by_printed_ones():
    from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
    from helper.runtime_object import ImmutableKeyedCollection
    from game.stat_type import STAT_ABILITIES
    state = board()
    card = add(state, "Creature")
    granted = ActivatedAbilityDefinition(key="granted", sorcery_speed=True)
    card.set_base_stat(STAT_ABILITIES, ImmutableKeyedCollection({granted.key: granted}))
    row = find(observation(state, state.players[0]), card)
    assert row["abilities"][0]["key"] == "granted"
    assert row["abilities"][0]["sorcery_speed"]
