"""The first DFS branch may bypass symmetry, but never complete search."""
from types import SimpleNamespace

import pytest

from game.enums import ManaType as M
from game.mana.mana_generator import ManaGenerator, ManaSource
from game.mana.mana_value import ColoredSymbol, ManaValue, ManaRequirement, ManaRequirementFragment


def source(card, colors, key="tap", uses=1):
    return ManaSource(card, key, ManaValue((ColoredSymbol(frozenset(colors)),)),
                      ManaValue(), uses)


def requirement(*colors):
    return ManaRequirement(tuple(ManaRequirementFragment(frozenset(c), 1) for c in colors))


def board(sources):
    return SimpleNamespace(get_mana_sources=lambda player: sources,
                           can_activate=lambda card, key: True)


def forbid_symmetry(monkeypatch):
    from game.mana import symmetry
    monkeypatch.setattr(symmetry, "ManaSymmetry", lambda *args: pytest.fail("Unnecessary symmetry preparation"))


def test_shared_resource_capacity_rejects_before_symmetry(monkeypatch):
    forbid_symmetry(monkeypatch)
    card = object()
    state = board([source(card, [M.BLUE], "blue"), source(card, [M.WHITE], "white")])
    solver = ManaGenerator()
    assert solver.generate(requirement([M.BLUE], [M.WHITE]), state, None) is None
    assert solver.statistics.capacity_rejections == 1


@pytest.mark.parametrize("deduplicate", [False, True])
def test_first_branch_success_keeps_pool_preference_and_reservations(monkeypatch, deduplicate):
    forbid_symmetry(monkeypatch)
    reserved, usable = object(), object()
    state = board([source(reserved, [M.BLUE]), source(usable, [M.BLUE])])
    solver = ManaGenerator(deduplicate_equivalent=deduplicate)
    result = solver.generate(requirement([M.BLUE], [M.BLUE]), state, None,
                             reserved={reserved}, mana_pool={M.BLUE: 1})
    assert [step.source for step in result.steps] == [None, usable]
    assert solver.statistics.greedy_successes == 1


def test_failed_first_branch_restores_capacity_for_backtracking():
    a, b = object(), object()
    state = board([source(a, [M.WHITE, M.BLUE]), source(b, [M.WHITE, M.BLACK])])
    solver = ManaGenerator()
    result = solver.generate(requirement([M.WHITE, M.BLUE], [M.BLUE, M.RED]), state, None)
    assert [(step.source, step.produces) for step in result.steps] == [(b, M.WHITE), (a, M.BLUE)]
    assert solver.statistics.greedy_successes == 0
    assert solver.statistics.states_visited > 2


def test_repeated_search_observes_current_activation_legality():
    a, b = object(), object()
    state = board([source(a, [M.BLUE]), source(b, [M.BLUE])])
    solver = ManaGenerator()
    req = requirement([M.BLUE])
    assert solver.generate(req, state, None).steps[0].source is a
    state.can_activate = lambda card, key: card is b
    assert solver.generate(req, state, None).steps[0].source is b


def test_unbounded_source_remains_usable_multiple_times(monkeypatch):
    forbid_symmetry(monkeypatch)
    card = object()
    solver = ManaGenerator()
    result = solver.generate(requirement([M.BLUE], [M.BLUE], [M.BLUE]),
                             board([source(card, [M.BLUE], uses=None)]), None)
    assert len(result.steps) == 3 and all(step.source is card for step in result.steps)
