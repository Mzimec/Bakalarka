import pytest

from game.enums import CardType, CardSubtype, ZoneType, ManaType, CounterType, TurnPhase
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.rules import lands
from game.stat_type import STAT_TYPES


@pytest.fixture
def board():
    player = Player([], ScriptedController())
    state = State([player, Player([], ScriptedController())])
    card = Card(CardDefinition("Plains", types=frozenset({CardType.LAND}),
                               subtypes=frozenset({CardSubtype.PLAINS})), player)
    player.add_card(card, ZoneType.BATTLEFIELD, state)
    state.synchronise_registers()
    return state, card


def test_native_mapping_is_shared_and_immutable(board, monkeypatch):
    state, card = board
    card.__dict__.pop("_ability_definition_cache", None)
    builds = []
    original = Card._build_ability_defs

    def observe(self, state):
        builds.append(self)
        return original(self, state)

    monkeypatch.setattr(Card, "_build_ability_defs", observe)
    first = card.get_ability_defs(state)
    assert card.get_ability_defs(state) is first
    card.is_tapped = True
    state.turn.phase = TurnPhase.END_STEP
    card.owner.health -= 1
    assert card.get_ability_defs(state) is first
    assert builds == [card]
    with pytest.raises(TypeError):
        first["injected"] = None


def test_live_type_mutation_and_rollback(board):
    state, card = board
    initial = card.get_ability_defs(state)
    checkpoint = RuntimeCheckpoint(state)
    card.set_base_stat(STAT_TYPES, frozenset({CardType.CREATURE}))
    assert "mana_white" not in card.get_ability_defs(state)
    assert lands.PLAY_LAND_DEFINITION.key not in card.get_ability_defs(state)
    checkpoint.rollback()
    assert card.get_ability_defs(state) is initial


def test_raw_modifiers_and_instance_readers_stay_live(board, monkeypatch):
    state, card = board
    first = card.get_ability_defs(state)
    card.modifier_sources[1].counters.data[CounterType.PLUS_ONE] = 1
    assert card.get_ability_defs(state) is not first
    card.modifier_sources[1].counters.data.clear()
    selected = [CardSubtype.ISLAND]
    monkeypatch.setattr(card, "get_subtypes", lambda state: frozenset(selected))
    assert "mana_blue" in card.get_ability_defs(state)
    selected[:] = [CardSubtype.PLAINS]
    assert "mana_white" in card.get_ability_defs(state)


def test_zone_predicates_and_zone_availability_are_not_cached(board):
    from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
    state, land = board
    enabled = [True]

    class Conditional(ActivatedAbilityDefinition):
        def is_usable_in_zone(self, zone):
            return enabled[0] and zone == ZoneType.BATTLEFIELD

    definition = Conditional(key="conditional")
    card = Card(CardDefinition("Unit", abilities=frozenset({definition})), land.owner)
    land.owner.add_card(card, ZoneType.BATTLEFIELD, state)
    descriptions = card.get_ability_defs(state)
    assert "conditional" in card.get_activatable_ability_defs(state)
    enabled[0] = False
    assert card.get_ability_defs(state) is descriptions
    assert "conditional" not in card.get_activatable_ability_defs(state)
    enabled[0] = True
    card.owner.move_card(card, ZoneType.HAND, state)
    assert "conditional" not in card.get_activatable_ability_defs(state)


def test_global_rule_metadata_and_replaced_helpers_stay_live(board, monkeypatch):
    state, card = board
    assert "mana_white" in card.get_ability_defs(state)
    monkeypatch.setitem(lands.BASIC_LAND_MANA, "PLAINS", ManaType.BLUE)
    assert "mana_blue" in card.get_ability_defs(state)
    selected = [ManaType.RED]
    monkeypatch.setattr(lands, "basic_land_abilities", lambda *a, **kw:
                        {lands.mana_ability(selected[0]).key: lands.mana_ability(selected[0])})
    assert "mana_red" in card.get_ability_defs(state)
    selected[:] = [ManaType.GREEN]
    assert "mana_green" in card.get_ability_defs(state)


def test_runtime_copy_rebinds_cache_inputs_and_keeps_worlds_independent(board):
    from game.ai.simulation_world import copy_world
    state, card = board
    original = card.get_ability_defs(state)
    cloned, _ = copy_world(state, {})
    other = cloned.card_register.get_by_key(card.key)
    assert set(other.get_ability_defs(cloned)) == set(original)
    assert other._ability_definition_cache[0] is other._stats
    other.set_base_stat(STAT_TYPES, frozenset({CardType.CREATURE}))
    assert "mana_white" not in other.get_ability_defs(cloned)
    assert card.get_ability_defs(state) is original


def test_subclasses_retain_dynamic_reads(board):
    state, card = board

    class CustomCard(Card):
        def get_types(self, state):
            return frozenset({CardType.LAND}) if self.owner.health > 10 else frozenset()

    custom = CustomCard(card.definition, card.owner)
    card.owner.add_card(custom, ZoneType.BATTLEFIELD, state)
    assert "mana_white" in custom.get_ability_defs(state)
    card.owner.health = 5
    assert "mana_white" not in custom.get_ability_defs(state)
    assert "mana_white" in card.get_ability_defs(state)


def test_immutable_container_with_dynamic_subtype_object_is_not_cached(board):
    from game.stat_type import STAT_SUBTYPES
    state, card = board

    class DynamicSubtype:
        name = "PLAINS"

    subtype = DynamicSubtype()
    card.set_base_stat(STAT_SUBTYPES, frozenset({subtype}))
    assert "mana_white" in card.get_ability_defs(state)
    subtype.name = "ISLAND"
    assert "mana_blue" in card.get_ability_defs(state)


def test_layer_projection_bypasses_cache_and_recovers_after_error(board, monkeypatch):
    state, card = board
    first = card.get_ability_defs(state)
    with monkeypatch.context() as patcher:
        patcher.setattr(state, "_refreshing_effects", True)
        assert card.get_ability_defs(state) is not first
        patcher.setattr(Card, "_build_ability_defs", lambda self, state: (_ for _ in ()).throw(ValueError("read failed")))
        with pytest.raises(ValueError, match="read failed"):
            card.get_ability_defs(state)
    assert card.get_ability_defs(state) is first
