"""Conservative card write sets for ordinary cost transactions.

Players, zones, stack, caches and query registers remain checkpointed. Only
unrelated card graphs are omitted. Unknown plans or runtime rules always use
the general checkpoint; eligibility never generates operations or pays costs.
As in generation/preflight, target and trigger predicates must be read-only.
Mutable delayed/state-trigger registrations retain the general checkpoint.
"""
from ...game_state.card import Card, CardRuntimeState
from ...game_state.state import State
from ...game_state.player import Player
from ...game_state.registers.player_register import PlayerRegister
from ...game_state.registers.card_register import (
    CardRegister, IK_HAS_TRIGGERS, IK_STATIC_REPLACEMENT,
    IK_ZONE, IK_DYNAMIC_CHARACTERISTICS,
)
from ...stat_type import STAT_TRIGGERS
from ...enums import ZoneType
from ..data_structs.game_action import (
    ScheduledResolution, FixedExecutionPlan, AbilityExecutionPlan, ManaAbilityAction,
    AbilityAction,
)
from ..data_structs.ability import (
    ActivatedAbilityDefinition, CastSpellAbilityDefinition, ManaAbilityDefinition,
    TriggerAbilityDefinition,
)
from ..card_effects import MoveSourceEffect, TapSourceEffect
from ..mana_effects import AddManaEffect, AddManaOperation, SpendManaOperation, PayLifeOperation
from ..mana_activation import ManaActivationEventOperation
from ...operations.card_operations import MoveCardOperation, TapCardOperation
from ..data_structs.operation import PassPriorityOperation
from ...rules.permanents import LoyaltyCostOperation


_EFFECTS = (MoveSourceEffect, TapSourceEffect, AddManaEffect)
_OPERATIONS = (MoveCardOperation, TapCardOperation, AddManaOperation,
               SpendManaOperation, PayLifeOperation, ManaActivationEventOperation,
               PassPriorityOperation, LoyaltyCostOperation)
_ABILITIES = (ActivatedAbilityDefinition, CastSpellAbilityDefinition, ManaAbilityDefinition)


def stack_only_cost(state, action, resolutions):
    """An ordinary trigger with an empty cost only appends to the stack.

    Its cost validator is the native no-op; no event or effect is executed in
    this transaction. Target legality was checked by ActionProcessor already.
    Trigger registration flags are nevertheless restored if stack routing fails.
    """
    from ..game_stack import GameStack
    from ..triggers.runtime_triggers import RuntimeTrigger
    if (type(action) is not AbilityAction or action.trigger_event is None
            or type(action.ability) is not TriggerAbilityDefinition
            or "validation_error" in vars(action.ability)
            or not action.uses_stack or type(state.stack) is not GameStack
            or len(resolutions) != 2):
        return None
    cost, effect = resolutions
    if (type(cost) is not ScheduledResolution or type(effect) is not ScheduledResolution
            or not cost.context.is_cost or cost.context.uses_stack
            or not effect.context.uses_stack or cost.context.targets
            or (cost.context.effects is not None and cost.context.effects.sequence)
            or cost.context.ability is not action.ability
            or action.ability.loyalty_cost is not None):
        return None
    plan = cost.generator
    if type(plan) is FixedExecutionPlan:
        if type(plan.operations) not in (tuple, list) or plan.operations:
            return None
    elif type(plan) is AbilityExecutionPlan:
        if plan.effects.sequence or plan.mana_solver_result is not None or plan.binding:
            return None
    else:
        return None
    if not _native(plan, (FixedExecutionPlan, AbilityExecutionPlan)):
        return None
    registration = cost.context.trigger_registration
    if registration is not None and type(registration) is not RuntimeTrigger:
        return None
    return (state.stack.items,) if registration is None else (state.stack.items, registration)


def _native(instance, classes):
    """Do not apply a built-in write contract to an instance override."""
    return type(instance) in classes and not any(
        callable(value) for value in vars(instance).values()
    )


def card_write_scope(state, resolutions):
    """Return touched cards, or None when the transaction needs a full snapshot.

    This first scope supports mana/life/tap payments and non-entry zone moves.
    Rules capable of introducing additional mutations (including mana triggers)
    deliberately retain the full fallback. Read only already committed indexes:
    synchronizing here would mutate the state before its rollback boundary.
    """
    if (type(state) is not State or type(state.card_register) is not CardRegister
            or type(state.player_register) is not PlayerRegister
            or any(type(player) is not Player for player in state.players)
            or state.card_register._dirty or state._effects_dirty
            or state._effects_checked_at != state.time_stamp
            or getattr(state, "runtime_triggers", ())):
        return None
    from helper.query_system.object_register import InMemoryObjectRegister
    from helper.query_system.query import EqQuery

    def indexed(key, value):
        return InMemoryObjectRegister.query(state.card_register, EqQuery(key, value))

    from .replacement_effects import ReplacementEffect, ReplacementEffectDefinition
    def irrelevant(definition):
        return (type(definition) is ReplacementEffectDefinition
                and definition.operation_types is not None
                and not any(issubclass(kind, definition.operation_types) for kind in _OPERATIONS))

    if any(type(rule) is not ReplacementEffect or not irrelevant(rule.definition)
           for rule in state.replacement_rules):
        return None
    for card in indexed(IK_STATIC_REPLACEMENT, True):
        if any(not irrelevant(rule) for rule in card.definition.replacement_effects):
            return None

    # Supported continuous graphs can only change battlefield memberships and
    # characteristics. Their sources and every possible target are captured.
    from ...game_state.continuous_dependencies import application_signature
    effects = tuple(state._cont_effect_manager._continuous_effects.values())
    if effects and getattr(state, "_continuous_application_signature", None) is None:
        # A clean refresh already classified this graph as unsupported. Do not
        # repeat card-by-card analysis only to reach the same full fallback.
        return None

    cards = set(indexed(IK_ZONE, ZoneType.BATTLEFIELD))
    cards.update(indexed(IK_HAS_TRIGGERS, True))
    cards.update(indexed(IK_DYNAMIC_CHARACTERISTICS, True))
    cards.update(state.card_register.layer_candidates._dynamic.values())
    from ...game_state.modifier import ContinuousEffect, ContinuousEffectDefinition
    for effect in effects:
        if type(effect) is not ContinuousEffect or type(effect.definition) is not ContinuousEffectDefinition:
            return None
        cards.add(effect.definition.source)
    pending = list(resolutions)
    seen = set()
    seen_actions = set()
    while pending:
        resolution = pending.pop()
        if id(resolution) in seen:
            return None
        seen.add(id(resolution))
        if type(resolution) is not ScheduledResolution:
            return None
        context = resolution.context
        if context.source is not None:
            cards.add(context.source)
        if context.ability is not None and not _native(context.ability, _ABILITIES):
            return None
        if context.uses_stack:
            # This generator is not executed until after this transaction commits.
            continue
        plan = resolution.generator
        if not _native(plan, (FixedExecutionPlan, AbilityExecutionPlan)):
            return None
        if type(plan) is FixedExecutionPlan:
            if type(plan.operations) not in (tuple, list):
                return None
            for operation in plan.operations:
                if not _native(operation, _OPERATIONS):
                    return None
                if type(operation) is MoveCardOperation and operation.destination == ZoneType.BATTLEFIELD:
                    return None
                if type(operation) is MoveCardOperation and _activates_rules(operation.card, operation.destination):
                    return None
                if operation.context.source is not None:
                    cards.add(operation.context.source)
                if hasattr(operation, "card"):
                    cards.add(operation.card)
        elif type(plan) is AbilityExecutionPlan:
            for binding in plan.effects.sequence:
                effect = binding.effect
                if not _native(effect, _EFFECTS):
                    return None
                if type(effect) is MoveSourceEffect and effect.destination == ZoneType.BATTLEFIELD:
                    return None
                if type(effect) is MoveSourceEffect and _activates_rules(context.source, effect.destination):
                    return None
            if plan.mana_solver_result is not None:
                for action in plan.mana_solver_result.mana_plan:
                    if type(action) is not ManaAbilityAction or id(action) in seen_actions:
                        return None
                    seen_actions.add(id(action))
                    pending.extend(action.get_intents())
        else:
            return None

    # A zone change also detaches the source and anything attached to it.
    from ...game_state.layer_index_plan import modified_stats
    pending_cards = list(cards)
    checked = set()
    while pending_cards:
        card = pending_cards.pop()
        if card in checked:
            continue
        if (not _native(card, (Card,)) or type(card.state) is not CardRuntimeState
                or card._game_state is not state
                or modified_stats(state, card) is None):
            return None
        # Non-mana triggers only capture eligibility here; their effects run
        # after commit. Mana triggers can execute arbitrary effects mid-payment.
        if any(definition.is_mana_ability for definition in card.get_trigger_defs(state).values()):
            return None
        checked.add(card)
        related = list(card.state.attached.values())
        if card.attached_to is not None:
            related.append(card.attached_to)
        pending_cards.extend(related)
    if effects and application_signature(state, effects) is None:
        return None
    return frozenset(checked)


def _activates_rules(card, destination):
    """A move must not introduce previously inactive runtime callbacks."""
    if type(card) is not Card:
        return True
    return (any(destination in rule.active_zones for rule in card.definition.continuous_effects)
            or any(destination in rule.active_zones for rule in card.definition.replacement_effects)
            or any(destination in definition.allowed_zones
                   for definition in card.stats[STAT_TRIGGERS].base_value.values()))
