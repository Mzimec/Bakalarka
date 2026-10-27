"""Explicit engine boundaries that can be resumed in an isolated runtime."""
from dataclasses import dataclass

from game.enums import TurnPhase


@dataclass(frozen=True)
class PriorityDecisionBoundary:
    """A native priority prompt outside any cost/trigger transaction."""
    phase: TurnPhase
    player: int
    allow_sorcery_speed: bool


@dataclass(frozen=True)
class CombatDeclarationBoundary:
    """Phase-start event has run; declaration and deferred triggers remain.

    Native two-player combat asks exactly one attacker or blocker declaration.
    Capturing just State would lose the phase's executor-local trigger queue.
    This value carries that queue, without retaining an executor or controller.
    """
    phase: TurnPhase
    player: int
    pending_triggers: tuple


@dataclass(frozen=True)
class CombatDamageBatchBoundary:
    """All damage assignments committed; no batch operation has executed yet."""
    phase: TurnPhase
    operations: tuple
    pending_triggers: tuple


@dataclass(frozen=True)
class ReplacementOrderingFrame:
    """APNAP batch construction, before any replacement budget is consumed."""
    operations: tuple
    player_order: tuple[int, ...]
    position: int = 0
    ordered: tuple = ()


def is_combat_damage_order(request):
    """Only the original public combat batch, before replacement arbitration."""
    from game.game_actions.generation.decision_abstraction.auxiliary import ReplacementOrderRequest
    from game.game_actions.generation.decision_abstraction.order_frames import OrderNextRequest
    from game.game_actions.resolution.replacement_effects import affected_player
    from game.operations.card_operations import DamagePlayerOperation, DamageCreatureOperation
    if type(request) not in (ReplacementOrderRequest, OrderNextRequest) or request.state is None:
        return False
    state = request.state
    batch = getattr(state, "combat_damage_batch_boundary", None)
    frame = getattr(state, "replacement_order_boundary", None)
    if (type(batch) is not CombatDamageBatchBoundary or type(frame) is not ReplacementOrderingFrame
            or batch.phase != state.turn.phase or len(state.players) != 2
            or batch.phase not in (TurnPhase.FIRST_COMBAT_DAMAGE, TurnPhase.SECOND_COMBAT_DAMAGE)
            or not 0 <= frame.position < len(frame.player_order)
            or frame.player_order[frame.position] != request.player.idx
            or tuple(map(id, frame.operations)) != tuple(map(id, batch.operations))
            or any(type(op) not in (DamagePlayerOperation, DamageCreatureOperation) for op in batch.operations)):
        return False
    candidates = request.candidates if type(request) is ReplacementOrderRequest else request.frame.candidates
    if type(request) is OrderNextRequest and request.frame.kind != "ReplacementOrderRequest":
        return False
    group = tuple(op for op in batch.operations if affected_player(state, op) is request.player)
    return tuple(map(id, candidates)) == tuple(map(id, group))
