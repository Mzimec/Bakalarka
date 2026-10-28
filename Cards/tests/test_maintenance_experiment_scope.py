"""Diagnostic monkeypatches must never escape into ordinary runtime."""
import pytest
from unittest.mock import patch

from benchmarks.mcts_incremental_maintenance import maintenance
from game.game_state import Player
from game.game_state.registers.card_register import CardRegister
from game.game_state.registers.indexed_register import IndexedRegister
from game.game_state.registers.player_register import PlayerRegister


@pytest.mark.parametrize('fail', [False, True])
def test_candidate_paths_restore_original_methods_even_after_an_error(fail):
    def methods():
        return (CardRegister.register, IndexedRegister.synchronise,
                CardRegister.synchronise, PlayerRegister.synchronise,
                PlayerRegister.changed_index_values, Player.health, Player.has_lost,
                Player.failed_draw, Player.poison_counters)
    original = methods()
    try:
        with maintenance(True):
            assert all(a is not b for a, b in zip(original, methods()))
            if fail:
                raise ValueError('controlled benchmark failure')
    except ValueError:
        assert fail
    assert methods() == original
    with maintenance(False):
        assert methods() == original


def test_explicit_inherited_alias_cannot_bypass_the_candidate_path():
    from benchmarks.mcts_incremental_maintenance import synchronise
    original = IndexedRegister.synchronise
    with patch.object(CardRegister, 'synchronise', original):
        with maintenance(True):
            assert CardRegister.synchronise is synchronise
            assert PlayerRegister.synchronise is synchronise
        assert CardRegister.synchronise is original
