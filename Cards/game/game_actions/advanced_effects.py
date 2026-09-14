"""Effects and trigger conditions used by the expanded declarative demo cards."""

from .data_structs.effect import Effect
from .data_structs.ability import TriggerCondition
from .data_structs.operation import Operation
from .resolution.event_bus import GameEvent
from ..operations.card_operations import (
    MoveCardOperation,
    DrawCardOperation,
    DamageCreatureOperation,
)
from ..enums import ZoneType


class SourceTappedCondition(TriggerCondition):
    """!
    @brief Trigger when this ability's own source becomes tapped.
    """

    def matches(self, state, event):
        """!
        @brief Check whether the event represents a card being tapped.
        """
        return event.key == "card_tapped"

    def matches_trigger(self, trigger, state):
        """!
        @brief Require the tapped card to be the bound trigger source.
        """
        return (
            self.matches(state, trigger.event)
            and trigger.event.source is trigger.source
        )


class DrawOperation(DrawCardOperation):
    """!
    @brief Demo-specific alias for the standard draw-card operation.
    """

    pass


class DrawEffect(Effect):
    """!
    @brief Declarative effect that draws one card.
    """

    def to_operations(self, state, context):
        """!
        @brief Generate the draw operation for this effect.

        @param state Current game state.
        @param context Bound resolution context.
        @return Generator yielding one draw operation.
        """
        yield DrawOperation(context)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Draw a card."


class InstallShieldOperation(Operation):
    """!
    @brief Install a persistent damage-prevention replacement rule.

    Replaces any previous shield installed by the same source incarnation with
    a fresh runtime replacement effect.
    """

    def execute(self, state):
        """!
        @brief Install the source's controller damage shield.

        @param state Current game state.
        @return No direct game events.
        """
        from .resolution.replacement_effects import ReplacementEffect, prevent_damage

        # Keep at most one installed shield for this source. Reinstalling the
        # effect replaces its previous runtime instance instead of stacking it.
        state.replacement_rules[:] = [
            rule
            for rule in state.replacement_rules
            if not (
                isinstance(rule, ReplacementEffect)
                and rule.key == "guardian_shield"
                and rule.source is self.context.source
            )
        ]

        definition = prevent_damage(
            "guardian_shield",
            1,
            predicate=lambda current, effect, op: (
                op.target is effect.source.get_controller(current)
            ),
        )

        state.replacement_rules.append(
            definition.bind(self.context.source)
        )

        return []


class InstallShieldEffect(Effect):
    """!
    @brief Effect that installs the source's controller damage shield.
    """

    def to_operations(self, state, context):
        """!
        @brief Generate the operation that installs the replacement rule.
        """
        yield InstallShieldOperation(context)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "While on the battlefield, prevent 1 damage from each hit to your controller."


class DamageCreaturesEffect(Effect):
    """!
    @brief Deal fixed damage to each creature represented by a target binding.

    @var amount
        Damage dealt per selected target occurrence.
    @var slot_key
        Target slot containing the creatures to damage.
    """

    def __init__(self, key, amount, slot_key="target"):
        super().__init__(key)
        self.amount = amount
        self.slot_key = slot_key

    def to_operations(self, state, context):
        """!
        @brief Generate one damage operation for each chosen creature.

        Repeated selections represented by target multiplicity scale the damage
        dealt to that target.

        @param state Current game state.
        @param context Bound resolution context containing selected targets.
        """
        for group in context.targets.get(self.slot_key, {}).values():
            for target, count in group.items():
                yield DamageCreatureOperation(
                    context,
                    target,
                    self.amount * count,
                )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Deal {self.amount} damage to each chosen creature."


class TemporaryBuffOperation(Operation):
    """!
    @brief Give a fixed set of card incarnations +2/+2 until cleanup.

    The operation remembers each target's current zone revision so cards that
    later leave and re-enter the battlefield are not treated as the same
    targeted permanent.
    """

    def __init__(self, context, targets):
        super().__init__(context)
        self.targets = frozenset(targets)

    def execute(self, state):
        """!
        @brief Install a temporary continuous +2/+2 effect.

        @param state Current game state.
        @return Event indicating that the buff was installed.
        """
        from uuid import uuid4
        from ..game_state.modifier import (
            ContinuousEffect,
            ContinuousEffectDefinition,
            ContinuousEffectState,
            TimeStampDuration,
            TimeStamp,
            DynamicTargetingStrategy,
            AddIntModifier,
        )
        from ..target.target_spec import QueryTargetSpec
        from ..game_state.registers.card_register import IK_KEY, IK_ZONE
        from helper.query_system.query import InQuery, EqQuery
        from ..stat_type import STAT_POWER, STAT_TOUGHNESS
        from ..enums import TurnPhase

        # Capture target incarnations now. Reusing the same Python Card object
        # after a zone change must not make the new incarnation inherit the buff.
        incarnations = tuple(
            (card, card.zone_revision)
            for card in self.targets
        )

        def query(source, controller, current_state):
            """!
            @brief Select still-valid target incarnations on the battlefield.
            """
            return InQuery(
                IK_KEY,
                frozenset(
                    card.key
                    for card, revision in incarnations
                    if card.zone_revision == revision
                ),
            ) & EqQuery(
                IK_ZONE,
                ZoneType.BATTLEFIELD,
            )

        definition = ContinuousEffectDefinition(
            duration=TimeStampDuration(
                TimeStamp(
                    state.turn.number,
                    TurnPhase.CLEANUP,
                )
            ),
            source=self.context.source,
            created_at=state.time_stamp,
            targeting=DynamicTargetingStrategy(
                QueryTargetSpec(query)
            ),
            modifiers={
                STAT_POWER: [
                    AddIntModifier(2),
                ],
                STAT_TOUGHNESS: [
                    AddIntModifier(2),
                ],
            },
        )

        state.add_continuous_effect(
            ContinuousEffect(
                f"buff:{uuid4().hex}",
                definition,
                ContinuousEffectState(set()),
            )
        )

        return [
            GameEvent(
                "buff_applied",
                self.context.source,
                self.context.controller,
            )
        ]


class TemporaryBuffEffect(Effect):
    """!
    @brief Give the creatures in the `target` slot +2/+2 until cleanup.
    """

    def to_operations(self, state, context):
        """!
        @brief Generate the temporary buff operation for all chosen targets.
        """
        targets = {
            target
            for group in context.targets.get("target", {}).values()
            for target in group
        }

        yield TemporaryBuffOperation(
            context,
            targets,
        )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Chosen creatures get +2/+2 until cleanup."


class SacrificeEffect(Effect):
    """!
    @brief Sacrifice permanents selected through the `sacrifice` target slot.
    """

    def validation_error(self, state, context):
        """!
        @brief Validate that every selected permanent can still be sacrificed.

        @param state Current game state.
        @param context Bound resolution context.
        @return Validation message on failure, otherwise `None`.
        """
        for group in context.targets.get("sacrifice", {}).values():
            for target in group:
                if (
                    target.get_zone() != ZoneType.BATTLEFIELD
                    or target.get_controller(state) is not context.controller
                ):
                    return "Sacrifice a permanent you currently control."

        return None

    def to_operations(self, state, context):
        """!
        @brief Generate graveyard moves for all selected sacrifice targets.
        """
        for group in context.targets.get("sacrifice", {}).values():
            for target in group:
                yield MoveCardOperation(
                    context,
                    target,
                    ZoneType.GRAVEYARD,
                )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Sacrifice the chosen creature you control."