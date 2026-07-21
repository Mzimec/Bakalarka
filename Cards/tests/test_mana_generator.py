

from __future__ import annotations
import pytest
from unittest.mock import MagicMock
from enum import Enum, auto

# importy ze tvého projektu
from game.mana.mana_value import ManaValue, ManaRequirement, ManaRequirementFragment, ColoredSymbol
from game.mana.mana_generator import ManaGenerator, ManaSource, ManaPlan, ManaPlanStep
from game.enums import *


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_card(name: str) -> MagicMock:
    card = MagicMock()
    card.name = name
    card.__hash__ = lambda self: hash(name)
    card.__eq__ = lambda self, other: hasattr(other, 'name') and other.name == name
    return card


def make_mana_value(*types: ManaType, generic: int = 0) -> ManaValue:
    symbols = tuple(ColoredSymbol(mana=frozenset({t})) for t in types)
    return ManaValue(symbols=symbols, generic=generic)


def make_source(
    card: MagicMock,
    *produces_types: ManaType,
    generic: int = 0,
    costs_generic: int = 0,
    uses_remaining: int | None = 1,
    ability_key: str = "tap_ability"
) -> ManaSource:
    return ManaSource(
        source=card,
        ability_key=ability_key,
        produces=make_mana_value(*produces_types, generic=generic),
        costs=make_mana_value(generic=costs_generic),
        uses_remaining=uses_remaining
    )


def make_req(*specs: tuple[frozenset[ManaType], int]) -> ManaRequirement:
    frags = tuple(
        ManaRequirementFragment(allowed=allowed, amount=amount)
        for allowed, amount in specs
    )
    return ManaRequirement(frags=frags)


def any_mana(amount: int = 1) -> ManaRequirementFragment:
    return ManaRequirementFragment(allowed=frozenset(ManaType), amount=amount)


def colored(mana_type: ManaType, amount: int = 1) -> ManaRequirementFragment:
    return ManaRequirementFragment(allowed=frozenset({mana_type}), amount=amount)


def make_state(sources: list[ManaSource], usable: bool = True) -> MagicMock:
    state = MagicMock()
    state.get_mana_sources.return_value = sources
    state.can_activate.return_value = usable
    return state


# ── Testy ─────────────────────────────────────────────────────────────────────

class TestManaGenerator:

    def setup_method(self):
        self.gen = ManaGenerator()
        self.controller = MagicMock()

    def _generate(self, req, state, reserved=frozenset()):
        return self.gen.generate(req, state, self.controller, reserved)

    # -- prázdný požadavek --

    def test_empty_requirement_returns_empty_plan(self):
        state = make_state([])
        result = self._generate(ManaRequirement.empty(), state)
        assert result is not None
        assert result.steps == ()

    # -- základní barevné fragmenty --

    def test_single_green_fragment(self):
        forest = make_card("Forest")
        state = make_state([make_source(forest, ManaType.GREEN)])

        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 1
        assert result.steps[0].source == forest
        assert result.steps[0].produces == ManaType.GREEN

    def test_returns_none_wrong_color(self):
        island = make_card("Island")
        state = make_state([make_source(island, ManaType.BLUE)])

        req = ManaRequirement(frags=(colored(ManaType.RED),))
        result = self._generate(req, state)

        assert result is None

    def test_generic_satisfied_by_any_color(self):
        mountain = make_card("Mountain")
        state = make_state([make_source(mountain, ManaType.RED)])

        req = ManaRequirement(frags=(any_mana(),))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 1

    # -- více fragmentů --

    def test_two_different_colors(self):
        forest = make_card("Forest")
        island = make_card("Island")
        state = make_state([
            make_source(forest, ManaType.GREEN),
            make_source(island, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.GREEN),
            colored(ManaType.BLUE),
        ))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 2
        produced = {s.produces for s in result.steps}
        assert ManaType.GREEN in produced
        assert ManaType.BLUE in produced

    def test_same_source_not_used_twice_for_two_frags(self):
        forest = make_card("Forest")
        state = make_state([make_source(forest, ManaType.GREEN, uses_remaining=1)])

        req = ManaRequirement(frags=(
            colored(ManaType.GREEN),
            colored(ManaType.GREEN),
        ))
        result = self._generate(req, state)

        assert result is None

    def test_source_producing_multiple_generic(self):
        vault = make_card("Mana Vault")
        state = make_state([make_source(vault, generic=3)])

        req = ManaRequirement(frags=(any_mana(3),))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 3

    # -- reserved --

    def test_reserved_source_skipped(self):
        forest = make_card("Forest")
        state = make_state([make_source(forest, ManaType.GREEN)])

        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state, reserved=frozenset({forest}))

        assert result is None

    def test_reserved_source_skipped_but_other_used(self):
        forest_a = make_card("ForestA")
        forest_b = make_card("ForestB")
        state = make_state([
            make_source(forest_a, ManaType.GREEN),
            make_source(forest_b, ManaType.GREEN),
        ])

        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state, reserved=frozenset({forest_a}))

        assert result is not None
        assert result.steps[0].source == forest_b

    # -- prioritizace --

    def test_prefers_single_color_over_dual(self):
        """Plains by měl být tapnut před Tundrou pro {W}."""
        plains = make_card("Plains")
        tundra = make_card("Tundra")
        state = make_state([
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
            make_source(plains, ManaType.WHITE),
        ])

        req = ManaRequirement(frags=(colored(ManaType.WHITE),))
        result = self._generate(req, state)

        assert result is not None
        assert result.steps[0].source == plains

    def test_prefers_free_over_costly(self):
        """Forest by měl být preferován před Prism Starem který něco stojí."""
        forest = make_card("Forest")
        prism = make_card("Prism Star")
        state = make_state([
            make_source(prism, ManaType.GREEN, costs_generic=1),
            make_source(forest, ManaType.GREEN),
        ])

        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state)

        assert result is not None
        assert result.steps[0].source == forest

    def test_strict_frags_satisfied_before_generic(self):
        """
        {G}{1} — Forest by měl jít na {G}, Island na {1}.
        Ne naopak kde by Island šel na {G} a selhal.
        """
        forest = make_card("Forest")
        island = make_card("Island")
        state = make_state([
            make_source(forest, ManaType.GREEN),
            make_source(island, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.GREEN),
            any_mana(),
        ))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 2
        sources_used = {s.source for s in result.steps}
        assert forest in sources_used
        assert island in sources_used

    # -- unusable sources --

    def test_tapped_source_skipped(self):
        tapped = make_card("TappedForest")
        untapped = make_card("UntappedForest")
        state = make_state([
            make_source(tapped, ManaType.GREEN),
            make_source(untapped, ManaType.GREEN),
        ])
        state.can_activate.side_effect = (
            lambda card, key: card != tapped
        )

        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state)

        assert result is not None
        assert result.steps[0].source == untapped

    def test_all_sources_unusable_returns_none(self):
        forest = make_card("Forest")
        state = make_state([make_source(forest, ManaType.GREEN)], usable=False)

        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state)

        assert result is None

    # -- edge cases --

    def test_no_sources_returns_none_for_nonempty_req(self):
        state = make_state([])
        req = ManaRequirement(frags=(colored(ManaType.GREEN),))
        result = self._generate(req, state)
        assert result is None

    def test_dual_land_satisfies_both_colors(self):
        """Tundra sama o sobě může splnit {W} i {U} pro dvě různé fragmenty."""
        tundra = make_card("Tundra")
        tundra2 = make_card("Tundra2")
        state = make_state([
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
            make_source(tundra2, ManaType.WHITE, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.WHITE),
            colored(ManaType.BLUE),
        ))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 2
        produced = {s.produces for s in result.steps}
        assert ManaType.WHITE in produced
        assert ManaType.BLUE in produced


    # -- složitější výběr ze zdrojů --

    def test_three_colors_three_sources(self):
        """{W}{U}{B} — každý land tapne pro svou barvu."""
        plains = make_card("Plains")
        island = make_card("Island")
        swamp = make_card("Swamp")
        state = make_state([
            make_source(plains, ManaType.WHITE),
            make_source(island, ManaType.BLUE),
            make_source(swamp, ManaType.BLACK),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.WHITE),
            colored(ManaType.BLUE),
            colored(ManaType.BLACK),
        ))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 3
        produced = {s.produces for s in result.steps}
        assert produced == {ManaType.WHITE, ManaType.BLUE, ManaType.BLACK}

    def test_dual_lands_with_overlap_find_valid_assignment(self):
        """
        Zdroje: Tundra(W/U), Scrubland(W/B)
        Požadavek: {U}{B}
        Greedy by mohl selhat pokud tapne Tundru pro {W} — 
        správně musí tapnout Tundru pro {U} a Scrubland pro {B}.
        """
        tundra = make_card("Tundra")
        scrubland = make_card("Scrubland")
        state = make_state([
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
            make_source(scrubland, ManaType.WHITE, ManaType.BLACK),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.BLUE),
            colored(ManaType.BLACK),
        ))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 2
        produced = {s.produces for s in result.steps}
        assert ManaType.BLUE in produced
        assert ManaType.BLACK in produced

    def test_overlap_prefers_less_flexible_source(self):
        """
        Zdroje: Tundra(W/U), Plains(W)
        Požadavek: {W}{U}
        Správně: Plains pro {W}, Tundra pro {U}.
        Špatně: Tundra pro {W}, Plains nemůže splnit {U}.
        """
        tundra = make_card("Tundra")
        plains = make_card("Plains")
        state = make_state([
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
            make_source(plains, ManaType.WHITE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.WHITE),
            colored(ManaType.BLUE),
        ))
        result = self._generate(req, state)

        assert result is not None
        assert len(result.steps) == 2
        produced = {s.produces for s in result.steps}
        assert ManaType.WHITE in produced
        assert ManaType.BLUE in produced
        # Plains musí jít na WHITE, Tundra na BLUE
        plains_step = next(s for s in result.steps if s.source == plains)
        tundra_step = next(s for s in result.steps if s.source == tundra)
        assert plains_step.produces == ManaType.WHITE
        assert tundra_step.produces == ManaType.BLUE

    def test_colored_and_generic_overlap(self):
        """
        Zdroje: Forest(G), Island(U)
        Požadavek: {G}{1}
        Forest musí jít na {G}, Island na {1} — ne naopak.
        """
        forest = make_card("Forest")
        island = make_card("Island")
        state = make_state([
            make_source(forest, ManaType.GREEN),
            make_source(island, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.GREEN),
            any_mana(),
        ))
        result = self._generate(req, state)

        assert result is not None
        forest_step = next((s for s in result.steps if s.source == forest), None)
        island_step = next((s for s in result.steps if s.source == island), None)
        assert forest_step is not None
        assert island_step is not None
        assert forest_step.produces == ManaType.GREEN

    def test_four_sources_two_needed_picks_least_flexible(self):
        """
        Zdroje: Tundra(W/U), Scrubland(W/B), Plains(W), Island(U)
        Požadavek: {W}{U}
        Správně: Plains pro {W}, Island pro {U} — nejméně flexibilní zdroje.
        """
        tundra = make_card("Tundra")
        scrubland = make_card("Scrubland")
        plains = make_card("Plains")
        island = make_card("Island")
        state = make_state([
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
            make_source(scrubland, ManaType.WHITE, ManaType.BLACK),
            make_source(plains, ManaType.WHITE),
            make_source(island, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.WHITE),
            colored(ManaType.BLUE),
        ))
        result = self._generate(req, state)

        assert result is not None
        sources_used = {s.source for s in result.steps}
        assert plains in sources_used
        assert island in sources_used
        assert tundra not in sources_used
        assert scrubland not in sources_used

    def test_impossible_with_overlap(self):
        """
        Zdroje: Tundra(W/U)
        Požadavek: {W}{U}
        Jeden zdroj nemůže splnit dva fragmenty.
        """
        tundra = make_card("Tundra")
        state = make_state([
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.WHITE),
            colored(ManaType.BLUE),
        ))
        result = self._generate(req, state)

        assert result is None

    def test_generic_uses_least_flexible_source(self):
        """
        Zdroje: Forest(G), Tundra(W/U)
        Požadavek: {G}{1}
        Forest pro {G}, Tundra pro {1} — Forest je méně flexibilní pro generic.
        """
        forest = make_card("Forest")
        tundra = make_card("Tundra")
        state = make_state([
            make_source(forest, ManaType.GREEN),
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.GREEN),
            any_mana(),
        ))
        result = self._generate(req, state)

        assert result is not None
        sources_used = {s.source for s in result.steps}
        assert forest in sources_used
        assert tundra in sources_used

    def test_costly_source_used_as_last_resort(self):
        """
        Zdroje: Prism(G, costs 1), Forest(G)
        Požadavek: {G}{G}
        Forest pro první {G}, Prism až pro druhý.
        """
        forest = make_card("Forest")
        forest2 = make_card("Forest2")
        prism = make_card("Prism")
        state = make_state([
            make_source(prism, ManaType.GREEN, costs_generic=1),
            make_source(forest, ManaType.GREEN),
            make_source(forest2, ManaType.GREEN),
        ])

        req = ManaRequirement(frags=(
            colored(ManaType.GREEN),
            colored(ManaType.GREEN),
        ))
        result = self._generate(req, state)

        assert result is not None
        sources_used = {s.source for s in result.steps}
        assert forest in sources_used
        assert forest2 in sources_used
        assert prism not in sources_used

    def test_reserved_forces_use_of_dual(self):
        """
        Zdroje: Plains(W), Tundra(W/U)
        Reserved: Plains
        Požadavek: {W}
        Musí použít Tundru i když je méně flexibilní.
        """
        plains = make_card("Plains")
        tundra = make_card("Tundra")
        state = make_state([
            make_source(plains, ManaType.WHITE),
            make_source(tundra, ManaType.WHITE, ManaType.BLUE),
        ])

        req = ManaRequirement(frags=(colored(ManaType.WHITE),))
        result = self._generate(req, state, reserved=frozenset({plains}))

        assert result is not None
        assert result.steps[0].source == tundra
        assert result.steps[0].produces == ManaType.WHITE