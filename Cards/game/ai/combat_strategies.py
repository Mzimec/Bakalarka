"""Combat proposals and evaluation; selection pipelines own declaration legality."""
from __future__ import annotations

from collections.abc import Iterable
from itertools import islice
from typing import TYPE_CHECKING

from game.enums import CardType, ZoneType
from game.game_actions.generation.decision_abstraction.options import (
    DeclareAttackersOption, DeclareBlockersOption,
)
from game.game_actions.generation.decision_abstraction.selection_strategies import (
    FullBlockersStrategy, combat_view,
)

if TYPE_CHECKING:
    from game.game_actions.generation.decision_abstraction.requests import (
        DeclareAttackersRequest, DeclareBlockersRequest,
    )
    from game.game_state import State, Player, Card


class AllAttackersStrategy:
    """Propose positive-power attackers against the first opponent."""

    def generate(self, request: DeclareAttackersRequest) -> Iterable[DeclareAttackersOption]:
        state, player = request.state, request.player
        opponent = next(p for p in state.active_players if p is not player)
        yield DeclareAttackersOption({
            c: opponent for c in state.combat.legal_attackers(player)
            if (c.get_power(state) or 0) > 0
        })


class CombatEvaluator:
    """Estimate trades using visible power/toughness, without resolving combat."""

    def defenders(self, state: State, opponent: Player) -> tuple[Card, ...]:
        from helper.query_system.query import EqQuery
        from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_CONTROLLER, IK_TAPPED

        return state.query_cards(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_TYPE, CardType.CREATURE)
            & EqQuery(IK_CONTROLLER, opponent) & EqQuery(IK_TAPPED, False)
        )

    def safe_attacker(self, state: State, attacker: Card, defenders: Iterable[Card]) -> bool:
        return not any(
            (not attacker.has_keyword(state, "flying")
             or b.has_keyword(state, "flying") or b.has_keyword(state, "reach"))
            and (b.get_power(state) or 0) >= (attacker.get_toughness(state) or 0)
            for b in defenders
        )

    def attack_score(self, request: DeclareAttackersRequest, option: DeclareAttackersOption) -> float:
        state = request.state
        value = 0
        for opponent in state.active_players:
            if opponent is request.player:
                continue
            defenders = self.defenders(state, opponent)
            attackers = [c for c, target in option.declarations.items() if target is opponent]
            power = sum(c.get_power(state) or 0 for c in attackers)
            losses = sum((c.get_power(state) or 0) + (c.get_toughness(state) or 0)
                         for c in attackers if not self.safe_attacker(state, c, defenders))
            value += power - losses
            if not defenders and power >= opponent.health:
                value += 1000
        return value

    def block_score(self, request: DeclareBlockersRequest, option: DeclareBlockersOption) -> float:
        state, player = request.state, request.player
        attackers = combat_view(state).attackers
        declaration = option.declarations
        damage = sum(max(0, a.get_power(state) or 0)
                     for a in attackers if a not in declaration.values())
        value = -damage * (8 if damage >= player.health else 1)
        for attacker in attackers:
            group = [b for b, target in declaration.items() if target is attacker]
            if not group:
                continue
            if sum(b.get_power(state) or 0 for b in group) >= (attacker.get_toughness(state) or 0):
                value += (attacker.get_power(state) or 0) + (attacker.get_toughness(state) or 0)
            for blocker in group:
                if (attacker.get_power(state) or 0) >= (blocker.get_toughness(state) or 0):
                    value -= (blocker.get_power(state) or 0) + (blocker.get_toughness(state) or 0)
        return value


class ConservativeAttackersStrategy:
    """Offer no attack, estimated safe attackers, and all attackers."""

    def __init__(self, evaluator=None):
        self.evaluator = evaluator or CombatEvaluator()

    def generate(self, request: DeclareAttackersRequest) -> Iterable[DeclareAttackersOption]:
        all_attackers = next(AllAttackersStrategy().generate(request))
        safe = {}
        for attacker, opponent in all_attackers.declarations.items():
            defenders = self.evaluator.defenders(request.state, opponent)
            if self.evaluator.safe_attacker(request.state, attacker, defenders):
                safe[attacker] = opponent
        yield DeclareAttackersOption({})
        yield DeclareAttackersOption(safe)
        yield all_attackers


class GreedyBlockersStrategy:
    """Propose mandatory blocks first, then cover the largest threats."""

    def generate(self, request: DeclareBlockersRequest) -> Iterable[DeclareBlockersOption]:
        state, player = request.state, request.player
        combat = combat_view(state)
        result = {}
        for attacker in combat.attackers:
            if attacker.has_keyword(state, "must be blocked by all"):
                legal = [c for c in combat.legal_blockers(player, attacker) if c not in result]
                if not attacker.has_keyword(state, "menace") or len(legal) >= 2:
                    result.update((c, attacker) for c in legal)
        for attacker in sorted(combat.attackers, key=lambda c: -(c.get_power(state) or 0)):
            if attacker in result.values():
                continue
            candidates = [c for c in combat.legal_blockers(player, attacker) if c not in result]
            candidates.sort(key=lambda c: (-(c.get_power(state) or 0), c.command_id))
            count = 2 if attacker.has_keyword(state, "menace") else 1
            if len(candidates) >= count:
                result.update((c, attacker) for c in candidates[:count])
        yield DeclareBlockersOption(result)


class BoundedBlockersStrategy:
    """Include a greedy proposal before bounded exhaustive enumeration.

    The limit counts proposals, not legal results. A pipeline may reject all of
    them; callers must not interpret this as proof that no legal block exists.
    """

    def __init__(self, max_assignments=512):
        if type(max_assignments) is not int or max_assignments < 1:
            raise ValueError("Combat search budget must be positive.")
        self.max_assignments = max_assignments

    def generate(self, request: DeclareBlockersRequest) -> Iterable[DeclareBlockersOption]:
        yield from GreedyBlockersStrategy().generate(request)
        yield from islice(FullBlockersStrategy().generate(request), self.max_assignments)
