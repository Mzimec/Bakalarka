"""Executable Arena Beginner Set starter cards.

The project intentionally keeps card data declarative: a ``CardDefinition``
contains the printed characteristics while the small effect/condition objects
below provide the rules needed by the two starter decks used by the demo.  It
is a closed, well-tested vertical slice rather than a claim to implement every
Magic card or every Comprehensive Rule.
"""

from __future__ import annotations
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition, CastSpellAbilityDefinition

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from immutabledict import immutabledict

from game.enums import CardSubtype, CardType, CounterType, ManaType, TurnPhase, ZoneType
from game.game_state import Card, CardDefinition
from game.game_state.modifier import (
    AddIntModifier,
    AddSetModifier,
    ContinuousEffect,
    ContinuousEffectDefinition,
    ContinuousEffectState,
    DynamicTargetingStrategy,
    TimeStampDuration,
    TimeStamp,
)
from game.game_state.continuous_rules import StaticContinuousRule
from game.stat_type import STAT_KEYWORDS, STAT_POWER, STAT_TOUGHNESS
from game.game_actions.data_structs.ability import SubAbilityDefinition, TriggerAbilityDefinition, TriggerCondition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.game_action import ResolutionContext
from game.game_actions.data_structs.operation import Operation
from game.game_actions.card_effects import MoveSourceEffect, TapSourceEffect
from game.game_actions.permanent_effects import CreateTokenOperation, attachment_ability
from game.game_actions.resolution.event_bus import GameEvent
from game.game_actions.triggers.trigger_condition import EntersBattlefieldCondition, DiesCondition
from game.operations.card_operations import (
    DamageCreatureOperation,
    DamagePlayerOperation,
    DrawCardOperation,
    MoveCardOperation,
    TapCardOperation,
)
from game.target.target_spec import TargetSpec
from helper.query_system.query import EqQuery, InQuery, DifferenceQuery
from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_CONTROLLER, IK_SUBTYPE, IK_NAME, IK_KEY, IK_OWNER
from game.target.target_resolver import TargetResolver, TargetSlot
from game.target.target_selector import SingleTargetSelector
from game.rules.lands import basic_land


class PredicateTargetSpec(TargetSpec):
    """!
    @brief Target candidates selected by a state-aware predicate.
    """

    def __init__(self, predicate, *, query=None):
        self.predicate = predicate
        self.query = query

    def candidate_query(self, source, controller, state):
        query = EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
        if self.query is not None:
            query &= self.query(source, controller, state) if callable(self.query) else self.query
        return query

    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield candidate objects permitted by this target specification.
        """
        for candidate in state.query_cards(self.candidate_query(source, controller, state)):
            if reserved and candidate in reserved:
                continue
            if self.predicate(candidate, source, controller, state):
                yield candidate

    def is_valid_target(self, target, source, controller, state, reserved=None):
        return (
            (not reserved or target not in reserved)
            and state.card_register.contains(self.candidate_query(source, controller, state), target)
            and self.predicate(target, source, controller, state)
        )


class AnyTargetSpec(TargetSpec):
    """!
    @brief The ordinary Magic "any target" (player, creature, or planeswalker).
    """

    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield candidate objects permitted by this target specification.
        """
        for player in state.active_players:
            if not reserved or player not in reserved:
                yield player
        for card in state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
            & InQuery(IK_TYPE, frozenset({CardType.CREATURE, CardType.PLANESWALKER}))):
            if not reserved or card not in reserved:
                yield card


def _slot(key="target", spec=None):
    return TargetSlot(
        key,
        TargetResolver(spec or AnyTargetSpec(), SingleTargetSelector()),
        distinct_from=frozenset(),
    )


def _effect_subdef(effects: Iterable[Effect], slots=(), *, mana_cost=None):
    effects = tuple(effects)
    slot_keys = frozenset(slot.key for slot in slots)
    mapping = {}
    for effect in effects:
        effect_slot = getattr(effect, "slot_key", None)
        mapping[effect.key] = (
            frozenset({effect_slot})
            if effect_slot in slot_keys
            else slot_keys if len(effects) == 1 else frozenset()
        )
    return SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap(mapping)) if mapping else None,
        mana_cost=mana_cost,
        effects=frozenset(effects),
        slots=frozenset(slots),
    )


def _cast_ability(name, mana_cost, *, permanent=False, effects=(), slots=(), sorcery_speed=False):
    put_on_stack = MoveSourceEffect(f"{name}:put_on_stack", ZoneType.STACK)
    destination = ZoneType.BATTLEFIELD if permanent else ZoneType.GRAVEYARD
    leave_stack = MoveSourceEffect(f"{name}:to_{destination.name.lower()}", destination)
    return CastSpellAbilityDefinition(
        key=f"cast:{name}",

        sorcery_speed=sorcery_speed,
        allowed_zones=frozenset({ZoneType.HAND}),
        cost_subdefs=(_effect_subdef((put_on_stack,)),),
        action_subdefs=(_effect_subdef((*effects, leave_stack), slots=slots),),
    )


def _activated_ability(
    name, *, cost_effects=(), mana_cost=None, action_effects=(), slots=(), sorcery_speed=False
):
    cost = _effect_subdef(tuple(cost_effects), mana_cost=mana_cost)
    action = _effect_subdef(tuple(action_effects), slots=slots)
    return ActivatedAbilityDefinition(
        key=name,
        allowed_zones=frozenset({ZoneType.BATTLEFIELD}),
        sorcery_speed=sorcery_speed,
        cost_subdefs=(cost,),
        action_subdefs=(action,),
    )


from game.game_actions.life_effects import GainLifeOperation


class GainLifeEffect(Effect):
    def __init__(self, key, amount):
        super().__init__(key)
        self.amount = amount

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        yield GainLifeOperation(context, self.amount)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Gain {self.amount} life."


class PutCounterOperation(Operation):
    def __init__(self, context, card, counter, amount=1):
        super().__init__(context)
        self.card, self.counter, self.amount = card, counter, amount

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        if self.card.get_zone() != ZoneType.BATTLEFIELD:
            return []
        counters = self.card.state.counters
        counters[self.counter] = counters.get(self.counter, 0) + self.amount
        state.notify_card_changed(self.card)
        return [
            GameEvent(
                "counter_placed",
                self.card,
                self.context.controller,
                {"counter": self.counter.name, "amount": self.amount},
            )
        ]


class PutCounterEffect(Effect):
    def __init__(
        self, key, counter=CounterType.PLUS_ONE, amount=1, *, source=True, target_getter=None
    ):
        super().__init__(key)
        self.counter, self.amount, self.source, self.target_getter = (
            counter,
            amount,
            source,
            target_getter,
        )

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        if self.target_getter is not None:
            for target in self.target_getter(state, context):
                yield PutCounterOperation(context, target, self.counter, self.amount)
        elif self.source:
            # A source-relative trigger affects its original permanent only.
            # Preserve non-targeted resolution, but do not put counters on a
            # different incarnation represented by the same Python object.
            if not context.matches_source_incarnation():
                return
            yield PutCounterOperation(context, context.source, self.counter, self.amount)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Put a {self.counter.name} counter on this creature."


class DrawCardsEffect(Effect):
    def __init__(self, key, count=1):
        super().__init__(key)
        self.count = count

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        for _ in range(self.count):
            yield DrawCardOperation(context)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Draw {self.count} card(s)."


class TemporaryModifierOperation(Operation):
    def __init__(self, context, targets, modifiers, key="starter-temporary"):
        super().__init__(context)
        self.targets = tuple(targets)
        self.modifiers = {stat: list(values) for stat, values in modifiers.items()}
        self.effect_key = f"{key}:{uuid4().hex}"

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        incarnations = tuple((card, card.zone_revision) for card in self.targets)

        def fixed_targets(source, controller, current_state):
            return (
                card
                for card, revision in incarnations
                if card.zone_revision == revision and card.get_zone() == ZoneType.BATTLEFIELD
            )

        definition = ContinuousEffectDefinition(
            duration=TimeStampDuration(TimeStamp(state.turn.number, TurnPhase.CLEANUP)),
            source=self.context.source,
            created_at=state.time_stamp,
            targeting=DynamicTargetingStrategy(
                PredicateTargetSpec(
                    lambda card, _source, _controller, _state: any(
                        card is target and card.zone_revision == revision
                        for target, revision in incarnations
                    ),
                    query=InQuery(IK_KEY, frozenset(target.key for target, _ in incarnations)),
                )
            ),
            modifiers=self.modifiers,
        )
        state.add_continuous_effect(
            ContinuousEffect(self.effect_key, definition, ContinuousEffectState(set()))
        )
        return [
            GameEvent(
                "temporary_effect_applied",
                self.context.source,
                self.context.controller,
                {"targets": self.targets},
            )
        ]


class TemporaryModifierEffect(Effect):
    def __init__(self, key, modifiers, slot_key="target", target_getter=None):
        super().__init__(key)
        self.modifiers, self.slot_key, self.target_getter = modifiers, slot_key, target_getter

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        if self.target_getter is not None:
            targets = tuple(self.target_getter(state, context))
        elif not self.slot_key or not context.targets or self.slot_key not in context.targets:
            if not context.matches_source_incarnation():
                return
            targets = (context.source,)
        else:
            targets = tuple(
                target
                for group in context.targets.get(self.slot_key, {}).values()
                for target in group
            )
        if targets:
            yield TemporaryModifierOperation(context, targets, self.modifiers, self.key)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Apply a temporary modifier until cleanup."


class DamageTargetEffect(Effect):
    def __init__(self, key, amount, slot_key="target"):
        super().__init__(key)
        self.amount, self.slot_key = amount, slot_key

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        for group in context.targets.get(self.slot_key, {}).values():
            for target, count in group.items():
                operation_type = (
                    DamagePlayerOperation if target in state.players else DamageCreatureOperation
                )
                yield operation_type(context, target, self.amount * count)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Deal {self.amount} damage to the selected target."


class DamageDefenderEffect(Effect):
    """!
    @brief Damage the defending player or planeswalker from an attack event.
    """

    def __init__(self, key, amount):
        super().__init__(key)
        self.amount = amount

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        event = context.trigger_event
        defender = event.payload.get("defender") if event is not None else None
        if defender is None:
            return
        if defender in state.players:
            yield DamagePlayerOperation(context, defender, self.amount)
        elif defender.get_zone() == ZoneType.BATTLEFIELD:
            yield DamageCreatureOperation(context, defender, self.amount)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Deal {self.amount} damage to the defending player or planeswalker."


class DamageDefendingCreaturesEffect(Effect):
    """!
    @brief Siege Dragon's attack trigger (Walls are checked at resolution).
    """

    def __init__(self, key, amount):
        super().__init__(key)
        self.amount = amount

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        event = context.trigger_event
        defender = event.payload.get("defender") if event is not None else None
        if defender in state.players:
            defending = defender
        elif defender is not None:
            defending = defender.get_controller(state)
        else:
            return
        if state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
            & EqQuery(IK_CONTROLLER, defending) & EqQuery(IK_SUBTYPE, CardSubtype.WALL)):
            return
        for card in state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
            & EqQuery(IK_CONTROLLER, defending) & EqQuery(IK_TYPE, CardType.CREATURE)):
            if not card.has_keyword(state, "flying"):
                yield DamageCreatureOperation(context, card, self.amount)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Deal {self.amount} damage to each defending creature without flying."


class DestroyOpponentWallsEffect(Effect):
    def __init__(self, key):
        super().__init__(key)

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        for card in state.query_cards(DifferenceQuery(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_SUBTYPE, CardSubtype.WALL),
            (EqQuery(IK_CONTROLLER, context.controller),))):
            yield MoveCardOperation(context, card, ZoneType.GRAVEYARD)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Destroy all Walls your opponents control."


class CreateTokensEffect(Effect):
    def __init__(self, key, definition, count=1, count_getter=None):
        super().__init__(key)
        self.definition, self.count, self.count_getter = definition, count, count_getter

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        count = self.count_getter(state, context) if self.count_getter else self.count
        yield CreateTokenOperation(context, self.definition, count)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Create {self.count} {self.definition.name} token(s)."


class CreateAttackingTokensOperation(CreateTokenOperation):
    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        events = super().execute(state)
        event = self.context.trigger_event
        defender = event.payload.get("defender") if event is not None else None
        if defender is None or not hasattr(state, "combat"):
            return events
        for token in self.created:
            token.is_tapped = True
            state.combat.attackers[token] = defender
            state.combat._remember(token)
        return events


class CreateAttackingTokensEffect(CreateTokensEffect):
    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        count = self.count_getter(state, context) if self.count_getter else self.count
        yield CreateAttackingTokensOperation(context, self.definition, count)


class TapOpponentsEffect(Effect):
    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        controller = context.controller
        for card in state.query_cards(DifferenceQuery(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_TYPE, CardType.CREATURE),
            (EqQuery(IK_CONTROLLER, controller),))):
            yield TapCardOperation(context, card, tap_symbol=False)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Tap all creatures your opponents control."


class DestroyEffect(Effect):
    def __init__(self, key, slot_key="target", card_type=None):
        super().__init__(key)
        self.slot_key, self.card_type = slot_key, card_type

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        for group in context.targets.get(self.slot_key, {}).values():
            for card in group:
                if card.get_zone() == ZoneType.BATTLEFIELD and not card.has_keyword(
                    state, "indestructible"
                ):
                    yield MoveCardOperation(context, card, ZoneType.GRAVEYARD)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Destroy the selected permanent."


class ReturnSourceEffect(Effect):
    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        # Follow only the battlefield-to-graveyard transition that caused
        # this dies trigger, never a later visit to the same graveyard.
        if (
            context.source.get_zone() == ZoneType.GRAVEYARD
            and context.matches_source_incarnation(zone_changes=1)
        ):
            yield MoveCardOperation(context, context.source, ZoneType.HAND)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Return this card to its owner's hand."


class ScryOneEffect(Effect):
    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        yield ScryOneOperation(context)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Scry 1."


class ScryOneOperation(Operation):
    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        from game.cards.starter_support import ScryOperation

        return ScryOperation(self.context, 1).execute(state)


class SacrificeOneGoblinEffect(Effect):
    def validation_error(self, state, context):
        """!
        @brief Return a validation message when the requested action is not legal.
        """
        if not state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
            & EqQuery(IK_CONTROLLER, context.controller) & EqQuery(IK_SUBTYPE, CardSubtype.GOBLIN)):
            return "You need a Goblin to sacrifice."
        return None

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        goblin = next(iter(state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
            & EqQuery(IK_CONTROLLER, context.controller) & EqQuery(IK_SUBTYPE, CardSubtype.GOBLIN))))
        yield MoveCardOperation(context, goblin, ZoneType.GRAVEYARD)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Sacrifice a Goblin."


class AttackCondition(TriggerCondition):
    looks_back_in_time = False

    def __init__(self, *, source_only=False, controlled_only=True, power_at_most=None):
        self.source_only, self.controlled_only, self.power_at_most = (
            source_only,
            controlled_only,
            power_at_most,
        )

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event satisfies this condition.
        """
        if event.key != "attacker_declared":
            return False
        attacker = event.payload.get("attacker", event.source)
        if attacker is None or attacker.get_zone() != ZoneType.BATTLEFIELD:
            return False
        if self.power_at_most is not None and (attacker.get_power(state) or 0) > self.power_at_most:
            return False
        return True

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition using the bound trigger source and event.
        """
        event = trigger.event
        if not self.matches(state, event):
            return False
        attacker = event.payload.get("attacker", event.source)
        return (not self.source_only or attacker is trigger.source) and (
            not self.controlled_only or attacker.get_controller(state) is trigger.controller
        )


class OneOrMoreAttackCondition(TriggerCondition):
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event satisfies this condition.
        """
        return event.key == "attackers_declared" and bool(event.payload.get("attackers"))

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition using the bound trigger source and event.
        """
        return self.matches(state, trigger.event) and trigger.event.controller is trigger.controller


class LifeGainCondition(TriggerCondition):
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event satisfies this condition.
        """
        return event.key == "life_gained" and event.payload.get("amount", 0) > 0

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition using the bound trigger source and event.
        """
        return self.matches(state, trigger.event) and trigger.event.controller is trigger.controller


class EnteredSmallCreatureCondition(TriggerCondition):
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event satisfies this condition.
        """
        card = event.source
        return (
            event.key == "card_moved"
            and event.payload.get("to") == ZoneType.BATTLEFIELD.name
            and card is not None
            and CardType.CREATURE in card.get_types(state)
            and (card.get_power(state) or 0) <= 2
        )

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition using the bound trigger source and event.
        """
        if not self.matches(state, trigger.event):
            return False
        card = trigger.event.source
        return card is not trigger.source and card.get_controller(state) is trigger.controller


class BlockedCondition(TriggerCondition):
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event satisfies this condition.
        """
        return event.key == "attacker_blocked"

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition using the bound trigger source and event.
        """
        return self.matches(state, trigger.event) and trigger.event.source is trigger.source


class ConfrontAssaultAbility(CastSpellAbilityDefinition):
    def validation_error(self, source, controller, state):
        """!
        @brief Return a validation message when the requested action is not legal.
        """
        error = super().validation_error(source, controller, state)
        if error:
            return error
        combat = getattr(state, "combat", None)
        if combat is None or not any(
            defender is controller for defender in combat.attackers.values()
        ):
            return "Cast this spell only if a creature is attacking you."
        return None


def _trigger(name, condition, effects, slots=()):
    return TriggerAbilityDefinition(
        key=name,
        condition=condition,
        action_subdefs=(_effect_subdef(tuple(effects), slots=slots),),
    )


def _creature(
    name,
    cost,
    subtypes,
    power,
    toughness,
    *,
    keywords=(),
    effects=(),
    slots=(),
    triggers=(),
    abilities=(),
    continuous_effects=(),
    oracle_text="",
    color=ManaType.WHITE,
):
    cast = _cast_ability(name, cost, permanent=True)
    return CardDefinition(
        name=name,
        mana_cost=cost,
        colors=frozenset({color}),
        color_identity={color},
        types=frozenset({CardType.CREATURE}),
        subtypes=frozenset(subtypes),
        power=power,
        toughness=toughness,
        keywords=frozenset(keywords),
        abilities=frozenset({cast, *abilities}),
        triggers=frozenset(triggers),
        continuous_effects=tuple(continuous_effects),
        oracle_text=oracle_text,
    )


def _spell(
    name,
    cost,
    card_type,
    effects,
    *,
    slots=(),
    sorcery_speed=False,
    ability_cls=CastSpellAbilityDefinition,
    keywords=(),
    oracle_text="",
    color=ManaType.WHITE,
):
    ability = _cast_ability(name, cost, effects=effects, slots=slots, sorcery_speed=sorcery_speed)
    if ability_cls is not CastSpellAbilityDefinition:
        ability = ability_cls(**{**ability.__dict__})
    return CardDefinition(
        name=name,
        mana_cost=cost,
        colors=frozenset({color}),
        color_identity={color},
        types=frozenset({card_type}),
        keywords=frozenset(keywords),
        abilities=frozenset({ability}),
        oracle_text=oracle_text,
    )


# ---------------------------------------------------------------------------
# Token definitions shared by starter cards

WHITE_CAT = CardDefinition(
    "Cat",
    mana_cost=None,
    colors=frozenset({ManaType.WHITE}),
    types=frozenset({CardType.CREATURE}),
    subtypes=frozenset({CardSubtype.CAT}),
    power=1,
    toughness=1,
    keywords=frozenset({"lifelink"}),
)
WHITE_SPIRIT = CardDefinition(
    "Spirit",
    mana_cost=None,
    colors=frozenset({ManaType.WHITE}),
    types=frozenset({CardType.CREATURE}),
    subtypes=frozenset({CardSubtype.SPIRIT}),
    power=1,
    toughness=1,
    keywords=frozenset({"flying"}),
)
RED_GOBLIN = CardDefinition(
    "Goblin",
    mana_cost=None,
    colors=frozenset({ManaType.RED}),
    types=frozenset({CardType.CREATURE}),
    subtypes=frozenset({CardSubtype.GOBLIN}),
    power=1,
    toughness=1,
)


def _all_own_creatures(state, context):
    return state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
        & EqQuery(IK_CONTROLLER, context.controller) & EqQuery(IK_TYPE, CardType.CREATURE))


def _event_attacker(state, context):
    event = context.trigger_event
    attacker = event.payload.get("attacker", event.source) if event is not None else None
    return (attacker,) if attacker is not None else ()


def _event_attackers(state, context):
    event = context.trigger_event
    return tuple(card for card in event.payload.get("attackers", ())
                 if _event_object_is_current(event, card)) if event is not None else ()


def _event_entered(state, context):
    event = context.trigger_event
    return (event.source,) if event is not None and _event_object_is_current(event, event.source) else ()


def _event_object_is_current(event, card):
    """! @brief Reject a new incarnation of a non-targeted event object. """
    if card is None:
        return False
    revisions = event.payload.get("object_revisions", {})
    return card.zone_revision == revisions.get(id(card), card.zone_revision)


def _charmed_stray_targets(state, context):
    return tuple(card for card in state.query_cards(
        EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_NAME, "charmed stray")
        & EqQuery(IK_CONTROLLER, context.controller)) if card is not context.source)


def _other_attackers_targets(state, context):
    event = context.trigger_event
    attacker = event.payload.get("attacker", event.source) if event is not None else None
    return tuple(card for card in _all_own_creatures(state, context) if card is not attacker)


def _graveyard_gathering_count(state, context):
    return 2 + len(state.query_cards(EqQuery(IK_ZONE, ZoneType.GRAVEYARD)
        & EqQuery(IK_OWNER, context.controller) & EqQuery(IK_NAME, "goblin gathering")))


class _PowerAtMostSpec(PredicateTargetSpec):
    def __init__(self, maximum, *, controller=None):
        super().__init__(
            lambda card, source, active, state: CardType.CREATURE in card.get_types(state)
            and (controller is None or card.get_controller(state) is active)
            and (card.get_power(state) or 0) <= maximum,
            query=EqQuery(IK_TYPE, CardType.CREATURE)
        )


class _CombatCreatureSpec(PredicateTargetSpec):
    def __init__(self):
        super().__init__(
            lambda card, source, controller, state: CardType.CREATURE in card.get_types(state)
            and card.get_controller(state) is controller
            and (
                card in state.combat.attackers
                or card in state.combat.blockers
                or card in state.combat.blocked
            ),
            query=lambda source, controller, state: EqQuery(IK_TYPE, CardType.CREATURE) & EqQuery(IK_CONTROLLER, controller)
        )


class _ArtifactSpec(PredicateTargetSpec):
    def __init__(self):
        super().__init__(
            lambda card, source, controller, state: CardType.ARTIFACT in card.get_types(state),
            query=EqQuery(IK_TYPE, CardType.ARTIFACT)
        )


class _GoblinStaticSpec(PredicateTargetSpec):
    def __init__(self):
        super().__init__(
            lambda card, source, controller, state: card is not source
            and card.get_controller(state) is source.get_controller(state)
            and CardSubtype.GOBLIN in card.get_subtypes(state),
            query=lambda source, controller, state: EqQuery(IK_SUBTYPE, CardSubtype.GOBLIN) & EqQuery(IK_CONTROLLER, source.get_controller(state))
        )


class _VitalitySpec(PredicateTargetSpec):
    def __init__(self):
        super().__init__(
            lambda card, source, controller, state: card is source and controller.health >= 25,
            query=lambda source, controller, state: EqQuery(IK_KEY, source.key)
        )


class _OwnCreatureTargetSpec(PredicateTargetSpec):
    def __init__(self):
        super().__init__(
            lambda card, source, controller, state: card.get_controller(state) is controller
            and CardType.CREATURE in card.get_types(state),
            query=lambda source, controller, state: EqQuery(IK_TYPE, CardType.CREATURE) & EqQuery(IK_CONTROLLER, controller)
        )


class _NonFlyingOpponentCreatureSpec(PredicateTargetSpec):
    def __init__(self):
        super().__init__(
            lambda card, source, controller, state: card.get_controller(state) is not controller
            and CardType.CREATURE in card.get_types(state)
            and not card.has_keyword(state, "flying"),
            query=lambda source, controller, state: DifferenceQuery(EqQuery(IK_TYPE, CardType.CREATURE), (EqQuery(IK_CONTROLLER, controller),))
        )


WHITE_CREATURE_TARGET = _slot(
    "target",
    PredicateTargetSpec(
        lambda card, source, controller, state: CardType.CREATURE in card.get_types(state),
        query=EqQuery(IK_TYPE, CardType.CREATURE),
    ),
)
ANY_TARGET = _slot("target", AnyTargetSpec())
COMBAT_CREATURE_TARGET = _slot("target", _CombatCreatureSpec())
POWER_TWO_TARGET = _slot("target", _PowerAtMostSpec(2))
ARTIFACT_TARGET = _slot("target", _ArtifactSpec())


# ---------------------------------------------------------------------------
# White starter cards

CHARMED_STRAY = _creature(
    "Charmed Stray",
    "{W}",
    {CardSubtype.CAT},
    1,
    1,
    keywords={"lifelink"},
    triggers=(
        _trigger(
            "charmed_stray_etb",
            EntersBattlefieldCondition(source_only=True),
            (PutCounterEffect("charmed_stray_counters", target_getter=_charmed_stray_targets),),
        ),
    ),
    oracle_text="Lifelink\nWhen this creature enters, put a +1/+1 counter on each other creature you control named Charmed Stray.",
)
FENCING_ACE = _creature(
    "Fencing Ace",
    "{1}{W}",
    {CardSubtype.HUMAN, CardSubtype.SOLDIER},
    1,
    1,
    keywords={"double strike"},
    oracle_text="Double strike",
)

HALLOWED_PRIEST = _creature(
    "Hallowed Priest",
    "{1}{W}",
    {CardSubtype.HUMAN, CardSubtype.CLERIC},
    1,
    1,
    triggers=(
        _trigger(
            "hallowed_priest_life",
            LifeGainCondition(),
            (PutCounterEffect("hallowed_priest_counter"),),
        ),
    ),
    oracle_text="Whenever you gain life, put a +1/+1 counter on this creature.",
)

IMPASSIONED_ORATOR = _creature(
    "Impassioned Orator",
    "{1}{W}",
    {CardSubtype.HUMAN, CardSubtype.CLERIC},
    2,
    2,
    triggers=(
        _trigger(
            "impassioned_orator_etb",
            EntersBattlefieldCondition(
                another=True, controlled_only=True, card_type=CardType.CREATURE
            ),
            (GainLifeEffect("impassioned_orator_life", 1),),
        ),
    ),
    oracle_text="Whenever another creature you control enters, you gain 1 life.",
)

MOORLAND_INQUISITOR = _creature(
    "Moorland Inquisitor",
    "{1}{W}",
    {CardSubtype.HUMAN, CardSubtype.SOLDIER},
    2,
    2,
    abilities=(
        _activated_ability(
            "first_strike",
            mana_cost="{2}{W}",
            action_effects=(
                TemporaryModifierEffect(
                    "first_strike_until_cleanup",
                    {STAT_KEYWORDS: [AddSetModifier(frozenset({"first strike"}))]},
                ),
            ),
        ),
    ),
    oracle_text="{2}{W}: This creature gains first strike until end of turn.",
)

ANGEL_OF_VITALITY = _creature(
    "Angel of Vitality",
    "{2}{W}",
    {CardSubtype.ANGEL},
    2,
    2,
    keywords={"flying"},
    continuous_effects=(
        StaticContinuousRule(
            "vitality_bonus",
            _VitalitySpec(),
            {STAT_POWER: [AddIntModifier(2)], STAT_TOUGHNESS: [AddIntModifier(2)]},
        ),
    ),
    oracle_text="Flying\nIf you would gain life, you gain that much life plus 1 instead.\nThis creature gets +2/+2 as long as you have 25 or more life.",
)

from dataclasses import replace as replace_definition
from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition

ANGEL_OF_VITALITY = replace_definition(
    ANGEL_OF_VITALITY,
    replacement_effects=(
        ReplacementEffectDefinition(
            "vitality_gain",
            lambda state, effect, operation: isinstance(operation, GainLifeOperation)
            and operation.amount > 0
            and operation.context.controller is effect.source.get_controller(state),
            lambda state, effect, operation: (
                GainLifeOperation(operation.context, operation.amount + 1),
            ),
        ),
    ),
)

LEONIN_WARLEADER = _creature(
    "Leonin Warleader",
    "{2}{W}{W}",
    {CardSubtype.CAT, CardSubtype.SOLDIER},
    4,
    4,
    triggers=(
        _trigger(
            "warleader_attack",
            AttackCondition(source_only=True),
            (CreateAttackingTokensEffect("warleader_cats", WHITE_CAT, 2),),
        ),
    ),
    oracle_text="Whenever this creature attacks, create two 1/1 white Cat creature tokens with lifelink that are tapped and attacking.",
)

SERRA_ANGEL = _creature(
    "Serra Angel",
    "{3}{W}{W}",
    {CardSubtype.ANGEL},
    4,
    4,
    keywords={"flying", "vigilance"},
    oracle_text="Flying\nVigilance",
)

SPIRITUAL_GUARDIAN = _creature(
    "Spiritual Guardian",
    "{3}{W}{W}",
    {CardSubtype.SPIRIT},
    3,
    4,
    triggers=(
        _trigger(
            "guardian_etb",
            EntersBattlefieldCondition(source_only=True),
            (GainLifeEffect("guardian_life", 4),),
        ),
    ),
    oracle_text="When this creature enters, you gain 4 life.",
)

ANGELIC_GUARDIAN = _creature(
    "Angelic Guardian",
    "{4}{W}{W}",
    {CardSubtype.ANGEL},
    5,
    5,
    keywords={"flying"},
    triggers=(
        _trigger(
            "guardian_attack",
            OneOrMoreAttackCondition(),
            (
                TemporaryModifierEffect(
                    "guardian_indestructible",
                    {STAT_KEYWORDS: [AddSetModifier(frozenset({"indestructible"}))]},
                    target_getter=_event_attackers,
                ),
            ),
        ),
    ),
    oracle_text="Flying\nWhenever one or more creatures you control attack, they gain indestructible until end of turn.",
)

INSPIRING_COMMANDER = _creature(
    "Inspiring Commander",
    "{4}{W}{W}",
    {CardSubtype.HUMAN, CardSubtype.SOLDIER},
    1,
    4,
    triggers=(
        _trigger(
            "commander_small_etb",
            EnteredSmallCreatureCondition(),
            (GainLifeEffect("commander_life", 1), DrawCardsEffect("commander_draw", 1)),
        ),
    ),
    oracle_text="Whenever another creature you control with power 2 or less enters, you gain 1 life and draw a card.",
)

GORING_CERATOPS = _creature(
    "Goring Ceratops",
    "{5}{W}{W}",
    {CardSubtype.DINOSAUR},
    3,
    3,
    keywords={"double strike"},
    triggers=(
        _trigger(
            "ceratops_attack",
            AttackCondition(source_only=True),
            (
                TemporaryModifierEffect(
                    "ceratops_double_strike",
                    {STAT_KEYWORDS: [AddSetModifier(frozenset({"double strike"}))]},
                    target_getter=_other_attackers_targets,
                ),
            ),
        ),
    ),
    oracle_text="Double strike\nWhenever this creature attacks, other creatures you control gain double strike until end of turn.",
)

TACTICAL_ADVANTAGE = _spell(
    "Tactical Advantage",
    "{W}",
    CardType.INSTANT,
    (
        TemporaryModifierEffect(
            "tactical_advantage",
            {STAT_POWER: [AddIntModifier(2)], STAT_TOUGHNESS: [AddIntModifier(2)]},
        ),
    ),
    slots=(COMBAT_CREATURE_TARGET,),
    oracle_text="Target blocking or blocked creature you control gets +2/+2 until end of turn.",
)

CONFRONT_THE_ASSAULT = _spell(
    "Confront the Assault",
    "{4}{W}",
    CardType.INSTANT,
    (CreateTokensEffect("confront_spirits", WHITE_SPIRIT, 3),),
    ability_cls=ConfrontAssaultAbility,
    oracle_text="Cast this spell only if a creature is attacking you.\nCreate three 1/1 white Spirit creature tokens with flying.",
)

BOND_OF_DISCIPLINE = _spell(
    "Bond of Discipline",
    "{4}{W}",
    CardType.SORCERY,
    (
        TapOpponentsEffect("bond_tap"),
        TemporaryModifierEffect(
            "bond_lifelink",
            {STAT_KEYWORDS: [AddSetModifier(frozenset({"lifelink"}))]},
            target_getter=_all_own_creatures,
        ),
    ),
    sorcery_speed=True,
    oracle_text="Tap all creatures your opponents control. Creatures you control gain lifelink until end of turn.",
)

PACIFISM_MODIFIERS = immutabledict(
    {
        STAT_KEYWORDS: (AddSetModifier(frozenset({"can't attack", "can't block"})),),
    }
)
PACIFISM = CardDefinition(
    "Pacifism",
    mana_cost="{1}{W}",
    color_identity={ManaType.WHITE},
    colors=frozenset({ManaType.WHITE}),
    types=frozenset({CardType.ENCHANTMENT}),
    subtypes=frozenset({CardSubtype.AURA}),
    attach_mods=PACIFISM_MODIFIERS,
    abilities=frozenset({attachment_ability()}),
    oracle_text="Enchant creature\nEnchanted creature can't attack or block.",
)

ANGELIC_REWARD_MODIFIERS = immutabledict(
    {
        STAT_POWER: (AddIntModifier(3),),
        STAT_TOUGHNESS: (AddIntModifier(3),),
        STAT_KEYWORDS: (AddSetModifier(frozenset({"flying"})),),
    }
)
ANGELIC_REWARD = CardDefinition(
    "Angelic Reward",
    mana_cost="{3}{W}{W}",
    color_identity={ManaType.WHITE},
    colors=frozenset({ManaType.WHITE}),
    types=frozenset({CardType.ENCHANTMENT}),
    subtypes=frozenset({CardSubtype.AURA}),
    attach_mods=ANGELIC_REWARD_MODIFIERS,
    abilities=frozenset({attachment_ability()}),
    oracle_text="Enchant creature\nEnchanted creature gets +3/+3 and has flying.",
)


# ---------------------------------------------------------------------------
# Red starter cards

GOBLIN_GANG_LEADER = _creature(
    "Goblin Gang Leader",
    "{2}{R}{R}",
    {CardSubtype.GOBLIN, CardSubtype.WARRIOR},
    2,
    2,
    color=ManaType.RED,
    triggers=(
        _trigger(
            "gang_leader_etb",
            EntersBattlefieldCondition(source_only=True),
            (CreateTokensEffect("gang_leader_goblins", RED_GOBLIN, 2),),
        ),
    ),
    oracle_text="When this creature enters, create two 1/1 red Goblin creature tokens.",
)

GOBLIN_TRASHMASTER = _creature(
    "Goblin Trashmaster",
    "{2}{R}{R}",
    {CardSubtype.GOBLIN, CardSubtype.WARRIOR},
    3,
    3,
    color=ManaType.RED,
    continuous_effects=(
        StaticContinuousRule(
            "trashmaster_bonus",
            _GoblinStaticSpec(),
            {STAT_POWER: [AddIntModifier(1)], STAT_TOUGHNESS: [AddIntModifier(1)]},
        ),
    ),
    abilities=(
        _activated_ability(
            "destroy_artifact",
            cost_effects=(SacrificeOneGoblinEffect("sacrifice_goblin"),),
            action_effects=(DestroyEffect("trashmaster_destroy", "target"),),
            slots=(ARTIFACT_TARGET,),
        ),
    ),
    oracle_text="Other Goblins you control get +1/+1.\nSacrifice a Goblin: Destroy target artifact.",
)

GOBLIN_TUNNELER = _creature(
    "Goblin Tunneler",
    "{1}{R}",
    {CardSubtype.GOBLIN, CardSubtype.ROGUE},
    1,
    1,
    color=ManaType.RED,
    abilities=(
        _activated_ability(
            "tunnel",
            cost_effects=(TapSourceEffect("tunnel_tap"),),
            action_effects=(
                TemporaryModifierEffect(
                    "unblockable", {STAT_KEYWORDS: [AddSetModifier(frozenset({"unblockable"}))]}
                ),
            ),
            slots=(POWER_TWO_TARGET,),
        ),
    ),
    oracle_text="{T}: Target creature with power 2 or less can't be blocked this turn.",
)

IMMORTAL_PHOENIX = _creature(
    "Immortal Phoenix",
    "{4}{R}{R}",
    {CardSubtype.PHOENIX},
    5,
    3,
    color=ManaType.RED,
    keywords={"flying"},
    triggers=(
        _trigger(
            "phoenix_dies", DiesCondition(source_only=True), (ReturnSourceEffect("phoenix_return"),)
        ),
    ),
    oracle_text="Flying\nWhen this creature dies, return it to its owner's hand.",
)

MOLTEN_RAVAGER = _creature(
    "Molten Ravager",
    "{2}{R}",
    {CardSubtype.ELEMENTAL},
    0,
    4,
    color=ManaType.RED,
    abilities=(
        _activated_ability(
            "power_up",
            mana_cost="{R}",
            action_effects=(
                TemporaryModifierEffect("ravager_power", {STAT_POWER: [AddIntModifier(1)]}),
            ),
        ),
    ),
    oracle_text="{R}: This creature gets +1/+0 until end of turn.",
)

NEST_ROBBER = _creature(
    "Nest Robber",
    "{1}{R}",
    {CardSubtype.DINOSAUR},
    2,
    1,
    keywords={"haste"},
    color=ManaType.RED,
    oracle_text="Haste",
)

OGRE_BATTLEDRIVER = _creature(
    "Ogre Battledriver",
    "{2}{R}{R}",
    {CardSubtype.WARRIOR},
    3,
    3,
    color=ManaType.RED,
    triggers=(
        _trigger(
            "battledriver_etb",
            EntersBattlefieldCondition(
                another=True, controlled_only=True, card_type=CardType.CREATURE
            ),
            (
                TemporaryModifierEffect(
                    "battledriver_bonus",
                    {
                        STAT_POWER: [AddIntModifier(2)],
                        STAT_KEYWORDS: [AddSetModifier(frozenset({"haste"}))],
                    },
                    target_getter=_event_entered,
                ),
            ),
        ),
    ),
    oracle_text="Whenever another creature you control enters, that creature gets +2/+0 and gains haste until end of turn.",
)

SIEGE_DRAGON = _creature(
    "Siege Dragon",
    "{5}{R}{R}",
    {CardSubtype.DRAGON},
    5,
    5,
    color=ManaType.RED,
    keywords={"flying"},
    triggers=(
        _trigger(
            "siege_dragon_etb",
            EntersBattlefieldCondition(source_only=True),
            (DestroyOpponentWallsEffect("siege_walls"),),
        ),
        _trigger(
            "siege_dragon_attack",
            AttackCondition(source_only=True),
            (DamageDefendingCreaturesEffect("siege_blast", 2),),
        ),
    ),
    oracle_text="Flying\nWhen this creature enters, destroy all Walls your opponents control.\nWhenever this creature attacks, if defending player controls no Walls, it deals 2 damage to each creature without flying that player controls.",
)

TIN_STREET_CADET = _creature(
    "Tin Street Cadet",
    "{R}",
    {CardSubtype.GOBLIN},
    1,
    1,
    color=ManaType.RED,
    triggers=(
        _trigger(
            "cadet_blocked",
            BlockedCondition(),
            (CreateTokensEffect("cadet_goblin", RED_GOBLIN, 1),),
        ),
    ),
    oracle_text="Whenever this creature becomes blocked, create a 1/1 red Goblin creature token.",
)

VOLCANIC_DRAGON = _creature(
    "Volcanic Dragon",
    "{4}{R}{R}",
    {CardSubtype.DRAGON},
    4,
    4,
    keywords={"flying", "haste"},
    color=ManaType.RED,
    oracle_text="Flying, haste",
)

BURN_BRIGHT = _spell(
    "Burn Bright",
    "{2}{R}",
    CardType.INSTANT,
    (
        TemporaryModifierEffect(
            "burn_bright", {STAT_POWER: [AddIntModifier(2)]}, target_getter=_all_own_creatures
        ),
    ),
    oracle_text="Creatures you control get +2/+0 until end of turn.",
    color=ManaType.RED,
)

INESCAPABLE_BLAZE = _spell(
    "Inescapable Blaze",
    "{4}{R}{R}",
    CardType.INSTANT,
    (DamageTargetEffect("inescapable_damage", 6),),
    slots=(ANY_TARGET,),
    keywords={"can't be countered"},
    oracle_text="This spell can't be countered.\nInescapable Blaze deals 6 damage to any target.",
    color=ManaType.RED,
)

SHOCK = _spell(
    "Shock",
    "{R}",
    CardType.INSTANT,
    (DamageTargetEffect("shock_damage", 2),),
    slots=(ANY_TARGET,),
    oracle_text="Shock deals 2 damage to any target.",
    color=ManaType.RED,
)

STORM_STRIKE = _spell(
    "Storm Strike",
    "{R}",
    CardType.INSTANT,
    (
        TemporaryModifierEffect(
            "storm_strike",
            {
                STAT_POWER: [AddIntModifier(1)],
                STAT_KEYWORDS: [AddSetModifier(frozenset({"first strike"}))],
            },
        ),
        ScryOneEffect("storm_scry"),
    ),
    slots=(WHITE_CREATURE_TARGET,),
    oracle_text="Target creature gets +1/+0 and gains first strike until end of turn. Scry 1.",
    color=ManaType.RED,
)

GOBLIN_GATHERING = _spell(
    "Goblin Gathering",
    "{2}{R}",
    CardType.SORCERY,
    (CreateTokensEffect("goblin_gathering", RED_GOBLIN, 2, _graveyard_gathering_count),),
    sorcery_speed=True,
    oracle_text="Create a number of 1/1 red Goblin creature tokens equal to two plus the number of cards named Goblin Gathering in your graveyard.",
    color=ManaType.RED,
)

RAID_BOMBARDMENT = CardDefinition(
    "Raid Bombardment",
    mana_cost="{2}{R}",
    color_identity={ManaType.RED},
    colors=frozenset({ManaType.RED}),
    types=frozenset({CardType.ENCHANTMENT}),
    abilities=frozenset({_cast_ability("Raid Bombardment", "{2}{R}", permanent=True)}),
    triggers=frozenset(
        {
            _trigger(
                "raid_attack",
                AttackCondition(power_at_most=2),
                (DamageDefenderEffect("raid_damage", 1),),
            )
        }
    ),
    oracle_text="Whenever a creature you control with power 2 or less attacks, this enchantment deals 1 damage to the player or planeswalker that creature is attacking.",
)


# A single canonical catalog is used by deck validation and game setup.  The
# two starter maps make it convenient for callers to inspect just one colour.
WHITE_STARTER_CARDS = {
    card.name: card
    for card in (
        CHARMED_STRAY,
        FENCING_ACE,
        HALLOWED_PRIEST,
        IMPASSIONED_ORATOR,
        MOORLAND_INQUISITOR,
        ANGEL_OF_VITALITY,
        LEONIN_WARLEADER,
        SERRA_ANGEL,
        SPIRITUAL_GUARDIAN,
        ANGELIC_GUARDIAN,
        INSPIRING_COMMANDER,
        GORING_CERATOPS,
        TACTICAL_ADVANTAGE,
        CONFRONT_THE_ASSAULT,
        BOND_OF_DISCIPLINE,
        PACIFISM,
        ANGELIC_REWARD,
    )
}
RED_STARTER_CARDS = {
    card.name: card
    for card in (
        GOBLIN_GANG_LEADER,
        GOBLIN_TRASHMASTER,
        GOBLIN_TUNNELER,
        IMMORTAL_PHOENIX,
        MOLTEN_RAVAGER,
        NEST_ROBBER,
        OGRE_BATTLEDRIVER,
        SIEGE_DRAGON,
        TIN_STREET_CADET,
        VOLCANIC_DRAGON,
        BURN_BRIGHT,
        INESCAPABLE_BLAZE,
        SHOCK,
        STORM_STRIKE,
        GOBLIN_GATHERING,
        RAID_BOMBARDMENT,
    )
}
STARTER_CARDS = {**WHITE_STARTER_CARDS, **RED_STARTER_CARDS}
STARTER_CATALOG = {
    **{name: basic_land(name) for name in ("Plains", "Island", "Swamp", "Mountain", "Forest")},
    **STARTER_CARDS,
}


def starter_catalog():
    """!
    @brief Return a fresh name-to-definition mapping for Arena starter games.
    """
    from game.cards.starter_extensions import extension_catalog

    from game.cards.rules_text import starter_rules_text
    from dataclasses import replace

    rules = starter_rules_text()
    return {
        name: replace(definition, oracle_text=rules[name] or "No printed abilities.")
        for name, definition in {**STARTER_CATALOG, **extension_catalog()}.items()
    }


__all__ = [
    "WHITE_CAT",
    "WHITE_SPIRIT",
    "RED_GOBLIN",
    "WHITE_STARTER_CARDS",
    "RED_STARTER_CARDS",
    "STARTER_CARDS",
    "STARTER_CATALOG",
    "starter_catalog",
]
