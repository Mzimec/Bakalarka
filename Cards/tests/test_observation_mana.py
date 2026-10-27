"""Reuse display computation without retaining visibility or mutable rules."""
import pytest

from game.ai.observation import observation
from game.ai.observation_mana import native_mana_projection
from benchmarks.mcts_world_latency import mana_projection_experiment
from game.enums import ZoneType
from game.game_state.stat import ModifiableReferenceStat
from game.mana.mana_value import ManaValue, ImmutableManaValue
from game.stat_type import STAT_GENERIC_COST_REDUCTION, STAT_MANA_COST
from test_player_observation import board, add


def views(state):
    return [observation(state, p, update_knowledge=False) for p in state.players]


def assert_reference(state):
    expected = views(state)
    with mana_projection_experiment():
        assert views(state) == expected
    return expected


@pytest.mark.parametrize('cost', [None, '', '{2}{U}', '{X}{R}', '{W/U}{W/P}{2/B}'])
def test_native_mana_views_match_reference_after_cost_reduction_x_and_zone_changes(cost):
    state = board()
    card = add(state, 'Cost')
    def replace_cost(value):
        card.set_base_stat(STAT_MANA_COST, value)
    replace_cost(None if cost is None else ImmutableManaValue(cost))
    assert_reference(state)
    # Repeat across viewers, then change effective inputs without clearing cache.
    assert_reference(state)
    assert state._card_snapshots.mana_view_hits > 0
    card.set_base_stat(STAT_GENERIC_COST_REDUCTION, 1)
    assert_reference(state)
    card.state.mana_x = 5
    card.set_zone(ZoneType.STACK, state)
    assert_reference(state)
    card.set_zone(ZoneType.GRAVEYARD, state)
    assert_reference(state)
    replace_cost(ImmutableManaValue('{3}{G}'))
    assert_reference(state)


def test_cost_order_is_part_of_key_and_custom_readers_stay_live():
    state = board()
    card = add(state, 'Cost')
    card.set_base_stat(STAT_MANA_COST, ImmutableManaValue('{U}{R}'))
    before = native_mana_projection(card, state)
    card.set_base_stat(STAT_MANA_COST, ImmutableManaValue('{R}{U}'))
    after = native_mana_projection(card, state)
    assert before[0] != after[0]
    card.get_casting_cost = lambda state: ImmutableManaValue('{G}')
    assert native_mana_projection(card, state) is None
    assert_reference(state)


def test_extension_stat_is_not_read_even_once_by_the_cache_adapter():
    state = board()
    card = add(state, 'Custom')
    calls = []
    class ExtensionStat(ModifiableReferenceStat):
        def value(self, state, modifiers=None):
            calls.append(1)
            return self.base_value
    from immutabledict import immutabledict
    card._stats = immutabledict({**card.stats,
        STAT_MANA_COST: ExtensionStat(ImmutableManaValue('{G}'), STAT_MANA_COST)})
    assert native_mana_projection(card, state) is None
    assert not calls


def test_mutable_costs_stay_live_and_no_private_card_identity_enters_cache():
    state = board()
    hidden = add(state, 'Secret', ZoneType.HAND, player=1)
    hidden.set_base_stat(STAT_MANA_COST, ManaValue('{2}{B}'))
    assert native_mana_projection(hidden, state) is None
    assert_reference(state)
    hidden.set_base_stat(STAT_MANA_COST, ImmutableManaValue('{2}{B}'))
    assert_reference(state)
    assert 'Secret' not in repr(state._card_snapshots._mana_views)


def test_mana_cache_is_bounded_and_world_copies_get_empty_derived_cache():
    state = board()
    card = add(state, 'Cost')
    for amount in range(270):
        card.set_base_stat(STAT_GENERIC_COST_REDUCTION, amount)
        native_mana_projection(card, state)
    assert 0 < len(state._card_snapshots._mana_views) <= 256
    from game.ai.simulation_world import copy_world
    copied, _ = copy_world(state, ())
    assert not getattr(copied._card_snapshots, '_mana_views', {})
    assert state._card_snapshots._mana_views
