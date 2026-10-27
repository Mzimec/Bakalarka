import pytest

from benchmarks.core_reads import board
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.game_state.stat import HasModifiableStats
from game.game_state.modifier import AddIntModifier
from game.game_state.registers.card_register import IK_DYNAMIC_CHARACTERISTICS, IK_POWER
from game.stat_type import STAT_POWER
from helper.query_system.query import EqQuery


MARKER = frozenset({IK_DYNAMIC_CHARACTERISTICS})


def test_unchanged_marker_does_not_invalidate_indexes_or_effects():
    state, card = board(0)
    register = state.card_register
    token = register.change_token
    pending = register.layer_candidates._pending.copy()
    register.mark_layer_changed(card, MARKER)
    assert not register._dirty and not state._effects_dirty
    assert register.change_token is token
    assert register.layer_candidates._pending == pending


def test_marker_checks_live_inputs_and_keeps_existing_wider_mutations():
    state, card = board(0)
    register = state.card_register
    # Deliberately bypass notifications: a live marker check must still notice.
    effects = card.modifier_sources[0].active_cont_effects
    effects._values.add("unbound")
    register.mark_layer_changed(card, MARKER)
    assert card.key in register._dirty
    register.synchronise()
    assert card in state.query_cards(EqQuery(IK_DYNAMIC_CHARACTERISTICS, True))
    effects._values.clear()
    register.mark_layer_changed(card, MARKER)
    register.synchronise()
    assert card not in state.query_cards(EqQuery(IK_DYNAMIC_CHARACTERISTICS, True))
    card.set_base_stat(STAT_POWER, 9)
    queued = register._dirty_indexes[card.key]
    register.mark_layer_changed(card, MARKER)
    assert card.key in register._dirty
    assert queued is None or queued <= register._dirty_indexes[card.key]
    assert card in state.query_cards(EqQuery(IK_POWER, 9))


def test_supported_final_graph_reuses_native_power_value(monkeypatch):
    state, card = board(3)
    assert card.get_power(state) == 5
    original = HasModifiableStats.get_stat
    reads = []

    def observed(self, key, state):
        if self is card and key == STAT_POWER:
            reads.append(key)
        return original(self, key, state)

    monkeypatch.setattr(HasModifiableStats, "get_stat", observed)
    before = getattr(state, "_continuous_graph_reuses", 0)
    state._effects_dirty = True
    state.refresh_continuous_effects()
    assert state._continuous_graph_reuses > before
    assert not reads
    assert not state._reusing_continuous_graph
    assert card in state.query_cards(EqQuery(IK_POWER, 5))


def test_graph_reuse_observes_base_replacement_and_rolls_back():
    state, card = board(2)
    assert card.get_power(state) == 4
    checkpoint = RuntimeCheckpoint(state)
    card.set_base_stat(STAT_POWER, 8)
    assert card in state.query_cards(EqQuery(IK_POWER, 10))
    assert not state._reusing_continuous_graph
    checkpoint.rollback()
    assert card in state.query_cards(EqQuery(IK_POWER, 4))
    assert not getattr(state, "_reusing_continuous_graph", False)
    state.get_cont_effect("bonus-0").modifiers[STAT_POWER][0] = AddIntModifier(7)
    state._effects_dirty = True
    assert card in state.query_cards(EqQuery(IK_POWER, 10))
    assert not state._reusing_continuous_graph


def test_exception_during_final_projection_releases_both_guards(monkeypatch):
    state, _ = board(1)
    def fail(*args):
        assert state._reusing_continuous_graph
        raise ValueError("projection failed")

    state._effects_dirty = True
    with monkeypatch.context() as context:
        context.setattr(state.card_register, "changed_index_values", fail)
        with pytest.raises(ValueError, match="projection failed"):
            state.refresh_continuous_effects()
    assert not state._refreshing_effects and not state._reusing_continuous_graph
    state.refresh_continuous_effects()
    assert not state._refreshing_effects


def test_empty_sync_still_refreshes_and_handles_new_dirty_work(monkeypatch):
    state, card = board(0)
    original = state.refresh_continuous_effects
    calls = []

    def refresh():
        if not calls:
            card.set_base_stat(STAT_POWER, 12)
        calls.append(True)
        original()

    monkeypatch.setattr(state, "refresh_continuous_effects", refresh)
    assert not state.card_register._dirty
    state.card_register.synchronise()
    assert calls
    assert card in state.query_cards(EqQuery(IK_POWER, 12))
