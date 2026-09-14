"""Preflight built-in costs without copying the mutable game or paying twice."""

from collections import defaultdict

from ...enums import ZoneType
from ...operations.card_operations import TapCardOperation, MoveCardOperation
from ..mana_effects import AddManaOperation, SpendManaOperation, PayLifeOperation


class CostResources:
    """!
    @brief Shared reservations for custom costs; hooks may only mutate this ledger.
    """

    def __init__(self):
        self.used = defaultdict(int)

    def reserve(self, resource, amount, available):
        if type(amount) is not int or amount < 0:
            return "A cost reservation must be a nonnegative integer."
        if self.used[resource] + amount > available:
            return f"Insufficient resource for cost: {resource}."
        self.used[resource] += amount
        return None


def validate_cost_operations(state, operations):
    from game.rules.permanents import LoyaltyCostOperation
    from ..data_structs.operation import PassPriorityOperation

    custom = CostResources()
    loyalty_paid = set()
    pools = {}
    life = {}
    tapped = set()
    moved = set()
    for operation in operations:
        player = operation.context.controller
        # Subclasses that override execute must explicitly provide their contract.
        kind = type(operation)
        if kind is LoyaltyCostOperation:
            source = operation.context.source
            if source in loyalty_paid or source in moved:
                return "A permanent cannot pay two loyalty costs in one activation."
            error = operation.validation_error(state)
            if error:
                return error
            loyalty_paid.add(source)
        elif kind in (AddManaOperation, SpendManaOperation):
            pool = pools.setdefault(player, defaultdict(int, dict(player.mana_pool)))
            if isinstance(operation, AddManaOperation):
                pool[operation.mana] += operation.amount
            else:
                if any(pool[mana] < amount for mana, amount in operation.payment.items()):
                    return "Insufficient mana to pay all costs."
                for mana, amount in operation.payment.items():
                    pool[mana] -= amount
        elif kind is PayLifeOperation:
            remaining = life.setdefault(player, player.health)
            if remaining < operation.amount:
                return "Insufficient life to pay all costs."
            life[player] -= operation.amount
        elif kind is TapCardOperation:
            card = operation.card
            if card in tapped or card.is_tapped or card in moved:
                return "The same permanent cannot pay a tap cost twice."
            if card.get_zone() != ZoneType.BATTLEFIELD or card.get_controller(state) is not player:
                return "Tap a battlefield permanent you control."
            if operation.tap_symbol and card.is_summoning_sick(state):
                return "A creature with summoning sickness cannot pay a tap cost."
            tapped.add(card)
        elif kind is MoveCardOperation:
            card = operation.card
            if card.is_token and card.token_left_battlefield:
                return "A token that left the battlefield cannot move again."
            if card in moved:
                return "The same object cannot pay two zone-change costs."
            if (
                card.owner.try_find_card(card.key) is not card
                or card.get_zone() == operation.destination
            ):
                return "A required cost object is no longer available."
            moved.add(card)
        elif kind is PassPriorityOperation:
            continue  # Compatibility: event-only cost used by old examples.
        else:
            reserve = getattr(operation, "reserve_cost", None)
            if reserve is None:
                return f"Unsupported cost operation {kind.__name__}: implement reserve_cost(state, resources)."
            error = reserve(state, custom)
            if error:
                return error
    return None


def validate_replaced_cost_operation(state, operation):
    """!
    @brief A replacement is an effect, so do not reimpose the original cost's form.

    Still require an executable operation covered by the rollback contract.
    In particular a replaced tap may affect another player's permanent.
    """
    from game.rules.permanents import LoyaltyCostOperation
    from ...operations.card_operations import (
        DamagePlayerOperation,
        DamageCreatureOperation,
        DrawCardOperation,
    )
    from ..data_structs.operation import PassPriorityOperation

    supported = (
        MoveCardOperation,
        TapCardOperation,
        AddManaOperation,
        SpendManaOperation,
        PayLifeOperation,
        LoyaltyCostOperation,
        DamagePlayerOperation,
        DamageCreatureOperation,
        DrawCardOperation,
        PassPriorityOperation,
    )
    if type(operation) not in supported:
        reserve = getattr(operation, "reserve_cost", None)
        if reserve is None:
            return f"Unsupported cost replacement operation {type(operation).__name__}: implement reserve_cost(state, resources)."
        error = reserve(state, CostResources())
        if error:
            return error
    validate = getattr(operation, "validation_error", None)
    return validate(state) if validate else None
