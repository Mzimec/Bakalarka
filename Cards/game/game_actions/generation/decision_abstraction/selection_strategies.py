"""Replaceable candidate enumeration; pipelines retain legality checks."""
from __future__ import annotations
from collections.abc import Iterable
from itertools import combinations, permutations, product
from typing import Protocol, TYPE_CHECKING
from copy import copy
from ...data_structs.decision_option import DecisionOption
from .options import (DeclareAttackersOption, DeclareBlockersOption, MulliganOption,
                      MulliganBottomOption, DiscardOption, AbilityResolutionOption)

if TYPE_CHECKING:
    from .requests import (DeclareAttackersRequest, DeclareBlockersRequest, MulliganRequest,
                           MulliganBottomRequest, DiscardRequest, AbilityResolutionRequest)


class SelectionStrategy[R, T: DecisionOption](Protocol):
    """!
    @brief Protocol for enumerating candidate decision options.
    """
    def generate(self, request: R) -> Iterable[T]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        ...


def combat_view(state: object) -> object:
    """!
    @brief Isolate the mutable participant bookkeeping used by combat validation.
    """
    combat = copy(state.combat)
    for name in ("attackers", "blockers", "blocked", "_identities", "_removed_defenders"):
        setattr(combat, name, getattr(combat, name).copy())
    combat.prune()
    return combat


class FullAttackersStrategy:
    """!
    @brief Strategy enumerating all attacker declarations.
    """
    def generate(self, request: DeclareAttackersRequest) -> Iterable[DeclareAttackersOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        from ....enums import CardType, ZoneType
        state, player = request.state, request.player
        attackers = state.combat.legal_attackers(player)
        opponents = tuple(p for p in state.active_players if p is not player)
        from helper.query_system.query import EqQuery, InQuery
        from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_CONTROLLER
        defenders = (*opponents, *state.query_cards(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_TYPE, CardType.PLANESWALKER)
            & InQuery(IK_CONTROLLER, frozenset(opponents))))
        for assignment in product((None, *defenders), repeat=len(attackers)):
            yield DeclareAttackersOption({a: d for a, d in zip(attackers, assignment) if d is not None})


class FullBlockersStrategy:
    """!
    @brief Strategy enumerating all blocker declarations.
    """
    def generate(self, request: DeclareBlockersRequest) -> Iterable[DeclareBlockersOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        combat, player = combat_view(request.state), request.player
        blockers = tuple(dict.fromkeys(b for a in combat.attackers for b in combat.legal_blockers(player, a)))
        choices = [(None, *(a for a in combat.attackers if combat.blocker_error(b, a, player) is None))
                   for b in blockers]
        for assignment in product(*choices):
            yield DeclareBlockersOption({b: a for b, a in zip(blockers, assignment) if a is not None})


class FullMulliganStrategy:
    """!
    @brief Strategy enumerating mulligan choices.
    """
    def generate(self, request: MulliganRequest) -> Iterable[MulliganOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        yield MulliganOption(False)
        if request.can_mulligan:
            yield MulliganOption(True)


class FullMulliganBottomStrategy:
    """!
    @brief Strategy enumerating mulligan-bottom choices.
    """
    def generate(self, request: MulliganBottomRequest) -> Iterable[MulliganBottomOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        for cards in permutations(tuple(request.player.hand.values()), request.count):
            yield MulliganBottomOption(cards)


class FullDiscardStrategy:
    """!
    @brief Strategy enumerating discard choices.
    """
    def generate(self, request: DiscardRequest) -> Iterable[DiscardOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        for cards in combinations(tuple(request.player.hand.values()), request.count):
            yield DiscardOption(cards)


class FullAbilityResolutionStrategy:
    """!
    @brief Strategy enumerating ability-resolution card choices.
    """
    def generate(self, request: AbilityResolutionRequest) -> Iterable[AbilityResolutionOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterable of candidate options.
        """
        for cards in combinations(request.candidates, request.count):
            yield AbilityResolutionOption(cards)
