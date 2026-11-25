"""Atomic combat declarations constructed through bounded per-creature menus.

Only the native independent-assignment rules use this protocol. Menace and
forced blocking retain the full declaration pipeline until they have a prefix
feasibility adapter. No partial assignment taps cards or publishes events.
"""
from dataclasses import dataclass, replace

from ...data_structs.decision_option import DecisionOption
from .requests import StateDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest
from .options import DeclareAttackersOption, DeclareBlockersOption


@dataclass(frozen=True)
class CombatDecisionFrame:
    """! @brief Immutable native assignment layout and the selected prefix. """
    kind: str
    creatures: tuple
    targets: tuple
    prefix: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'creatures', tuple(self.creatures))
        if type(self.targets) is not tuple or any(type(targets) is not tuple for targets in self.targets):
            object.__setattr__(self, 'targets', tuple(tuple(targets) for targets in self.targets))
        object.__setattr__(self, 'prefix', tuple(self.prefix))
        if (self.kind not in ('attackers', 'blockers') or len(self.creatures) != len(self.targets)
                or len(self.prefix) > len(self.creatures) or len(set(self.creatures)) != len(self.creatures)):
            raise ValueError('Invalid combat construction layout')
        for index, target in enumerate(self.prefix):
            if target is not None and not any(target is item for item in self.targets[index]):
                raise ValueError('Invalid combat construction prefix')

    @property
    def remaining_count(self):
        """! @brief Number of assignments before atomic declaration validation. """
        return len(self.creatures) - len(self.prefix)

    def advance(self, target):
        """! @brief Extend a prefix without touching the runtime combat state. """
        if not self.remaining_count:
            raise ValueError('Combat declaration is already complete')
        return replace(self, prefix=(*self.prefix, target))

    def finish(self, state, player):
        """! @brief Validate and return the complete ordinary declaration option. """
        if self.remaining_count:
            raise ValueError('Cannot execute an incomplete combat declaration')
        declarations = {c: t for c, t in zip(self.creatures, self.prefix) if t is not None}
        if self.kind == 'attackers':
            state.combat.validate_attackers(player, declarations)
            return DeclareAttackersOption(declarations)
        from .selection_strategies import combat_view
        combat_view(state).validate_blockers(player, declarations)
        return DeclareBlockersOption(declarations)

    @classmethod
    def capture(cls, request):
        """! @brief Build a polynomial layout, or retain the existing full protocol. """
        from game.rules.combat import CombatState
        from game.enums import CardType, ZoneType
        from .selection_pipelines import DeclareAttackersPipeline, DeclareBlockersPipeline
        from .selection_strategies import combat_view
        state, player = request.state, request.player
        if type(state.combat) is not CombatState:
            return None
        if type(request) is DeclareAttackersRequest:
            DeclareAttackersPipeline().validate_request(request)
            creatures = tuple(state.combat.legal_attackers(player))
            opponents = tuple(p for p in state.active_players if p is not player)
            from helper.query_system.query import EqQuery, InQuery
            from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_CONTROLLER
            targets = (*opponents, *state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                & EqQuery(IK_TYPE, CardType.PLANESWALKER) & InQuery(IK_CONTROLLER, frozenset(opponents))))
            return cls('attackers', creatures, (targets,) * len(creatures))
        if type(request) is not DeclareBlockersRequest:
            return None
        DeclareBlockersPipeline().validate_request(request)
        combat = combat_view(state)
        # A partial single block on menace can still become a legal multiblock.
        # Rejecting it here would silently remove legal complete declarations.
        if any(combat._keyword(a, keyword) for a in combat.attackers
               for keyword in ('menace', 'must be blocked by all')):
            return None
        creatures = tuple(dict.fromkeys(b for a in combat.attackers for b in combat.legal_blockers(player, a)))
        targets = tuple(tuple(a for a in combat.attackers if combat.blocker_error(b, a, player) is None)
                        for b in creatures)
        return cls('blockers', creatures, targets)


@dataclass(frozen=True)
class CombatNextOption(DecisionOption):
    """! @brief Assign this creature to a target, or keep it out of combat. """
    target: object = None


@dataclass(frozen=True)
class CombatNextRequest(StateDecisionRequest[CombatNextOption]):
    """! @brief One copyable construction boundary, not a priority window. """
    _frame: CombatDecisionFrame
    option_type = CombatNextOption

    @property
    def frame(self):
        """! @brief Runtime recipe; policy input uses the reviewed context adapter. """
        return self._frame


class CombatNextPipeline:
    """! @brief Offer only the next creature's linear-size native choice menu. """
    def generate(self, request, policy):
        if policy.pruning is not None or not request.frame.remaining_count:
            raise ValueError('Invalid combat prefix generation policy or completed frame')
        yield CombatNextOption()
        for target in request.frame.targets[len(request.frame.prefix)]:
            yield CombatNextOption(target)


def choose_combat(request, frame, decide):
    """! @brief Complete or resume an owned frame, restoring state even on abort. """
    previous = getattr(request.state, 'combat_decision_frame', None)
    if previous is not None:
        if (type(previous) is not CombatDecisionFrame or previous.kind != frame.kind
                or previous.creatures != frame.creatures or previous.targets != frame.targets):
            raise ValueError('Combat continuation layout diverged')
        frame = previous
    completed = False
    try:
        while frame.remaining_count:
            request.state.combat_decision_frame = frame
            result = decide(CombatNextRequest(request.state, request.player, frame))
            frame = frame.advance(result.value.target)
        result = frame.finish(request.state, request.player)
        completed = True
        return result
    finally:
        # A restored frame is a consumed continuation, not an outer live
        # callback. Keeping it after completion would contaminate the next
        # blocker declaration with this attacker's old construction recipe.
        if completed or previous is None:
            request.state.__dict__.pop('combat_decision_frame', None)
        else:
            request.state.combat_decision_frame = previous
