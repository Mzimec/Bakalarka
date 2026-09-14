"""Reusable predicates for declarative triggered abilities.

Custom conditions use ``event.payload['last_known']`` for the previous zone,
types and controller, and ``matches_trigger`` to inspect their own source.
"""

from dataclasses import dataclass

from ..data_structs.ability import TriggerAbility, TriggerCondition
from ...enums import CardType, ZoneType


def _zone_name(zone):
    """!
    @brief Normalize a zone value to its enum name when available.

    @param zone Zone enum or already-normalized value.
    @return Zone name or the original value.
    """
    return getattr(zone, "name", zone)


@dataclass(frozen=True)
class EventKeyCondition(TriggerCondition):
    """!
    @brief Match a specific game-event key.

    @var key
        Event key that must match.
    @var source_only
        Whether the event source must be the trigger's own source.
    """

    key: str
    source_only: bool = False

    def matches(self, state, event):
        """!
        @brief Check whether the event has the configured key.
        """
        return event.key == self.key

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition with access to the bound trigger source.
        """
        return self.matches(state, trigger.event) and (
            not self.source_only
            or trigger.event.source is trigger.source
        )


@dataclass(frozen=True)
class ZoneChangeCondition(TriggerCondition):
    """!
    @brief Match card movement between zones with optional source restrictions.

    Leaving-the-battlefield conditions use last-known information so controller
    and type checks observe the card as it existed before leaving.

    @var origin
        Required origin zone, or `None` for any origin.
    @var destination
        Required destination zone, or `None` for any destination.
    @var source_only
        Whether only the trigger's own source may match.
    @var another
        Whether the trigger's own source must be excluded.
    @var controlled_only
        Whether the moved card must be controlled by the trigger controller.
    @var card_type
        Optional required card type.
    """

    origin: ZoneType | None = None
    destination: ZoneType | None = None
    source_only: bool = False
    another: bool = False
    controlled_only: bool = False
    card_type: CardType | None = None

    @property
    def looks_back_in_time(self):
        """!
        @brief Whether this condition must inspect the pre-event trigger roster.

        Battlefield-leave triggers are detected using characteristics from
        immediately before the card left the battlefield.
        """
        return self.origin == ZoneType.BATTLEFIELD

    def matches(self, state, event):
        """!
        @brief Check whether the event represents the configured zone change.
        """
        return (
            event.key == "card_moved"
            and (
                self.origin is None
                or _zone_name(event.payload.get("from")) == self.origin.name
            )
            and (
                self.destination is None
                or _zone_name(event.payload.get("to")) == self.destination.name
            )
            and _zone_name(event.payload.get("from"))
            != _zone_name(event.payload.get("to"))
        )

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate source, controller and type restrictions for the trigger.

        Uses last-known information for battlefield-leave conditions so checks
        are not affected by characteristics changing after the zone transition.
        """
        event = trigger.event

        if not self.matches(state, event):
            return False

        if self.source_only and event.source is not trigger.source:
            return False

        if self.another and event.source is trigger.source:
            return False

        last_known = (
            event.payload.get("last_known")
            if self.looks_back_in_time
            else None
        )

        if self.controlled_only:
            controller = (
                last_known.controller
                if last_known is not None
                else event.source.get_controller(state)
            )

            if controller is not trigger.controller:
                return False

        if self.card_type is not None:
            types = (
                last_known.types
                if last_known is not None
                else event.source.get_types(state)
            )

            if self.card_type not in types:
                return False

        return True


class EntersBattlefieldCondition(ZoneChangeCondition):
    """!
    @brief Match cards entering the battlefield.
    """

    def __init__(
        self,
        *,
        source_only=False,
        another=False,
        controlled_only=False,
        card_type=None,
    ):
        """!
        @brief Create a battlefield-entry trigger condition.
        """
        super().__init__(
            destination=ZoneType.BATTLEFIELD,
            source_only=source_only,
            another=another,
            controlled_only=controlled_only,
            card_type=card_type,
        )


class LeavesBattlefieldCondition(ZoneChangeCondition):
    """!
    @brief Match cards leaving the battlefield.
    """

    def __init__(
        self,
        *,
        source_only=False,
        another=False,
        controlled_only=False,
        card_type=None,
    ):
        """!
        @brief Create a battlefield-leave trigger condition.
        """
        super().__init__(
            origin=ZoneType.BATTLEFIELD,
            source_only=source_only,
            another=another,
            controlled_only=controlled_only,
            card_type=card_type,
        )


class DiesCondition(ZoneChangeCondition):
    """!
    @brief Match creatures moving from the battlefield to the graveyard.
    """

    def __init__(
        self,
        *,
        source_only=False,
        another=False,
        controlled_only=False,
    ):
        """!
        @brief Create a creature-death trigger condition.
        """
        super().__init__(
            origin=ZoneType.BATTLEFIELD,
            destination=ZoneType.GRAVEYARD,
            source_only=source_only,
            another=another,
            controlled_only=controlled_only,
            card_type=CardType.CREATURE,
        )


@dataclass(frozen=True)
class StepCondition(TriggerCondition):
    """!
    @brief Match the beginning of a configured phase or step.

    @var phase
        Phase identifier that must begin.
    @var controller_turn_only
        Whether the phase must belong to the trigger controller's turn.
    """

    phase: object
    controller_turn_only: bool = False
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the configured phase has started.
        """
        return (
            event.key == "phase_started"
            and _zone_name(event.payload.get("phase"))
            == _zone_name(self.phase)
        )

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the phase condition for the bound trigger controller.
        """
        return self.matches(state, trigger.event) and (
            not self.controller_turn_only
            or trigger.event.controller is trigger.controller
        )


@dataclass(frozen=True)
class SpellCastCondition(TriggerCondition):
    """!
    @brief Match spell-cast events with optional controller/source restrictions.

    @var controlled_only
        Whether the spell must be controlled by the trigger controller.
    @var source_only
        Whether the cast spell must be the trigger's own source.
    """

    controlled_only: bool = False
    source_only: bool = False
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event represents a spell being cast.
        """
        return event.key == "spell_cast"

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate controller and source restrictions for the spell event.
        """
        return (
            self.matches(state, trigger.event)
            and (
                not self.controlled_only
                or trigger.event.controller is trigger.controller
            )
            and (
                not self.source_only
                or trigger.event.source is trigger.source
            )
        )


@dataclass(frozen=True)
class DamageDealtCondition(TriggerCondition):
    """!
    @brief Match positive damage events with optional combat/target restrictions.

    @var source_only
        Whether the damage source must be the trigger's own source.
    @var combat_only
        Whether only combat damage should match.
    @var to_player_only
        Whether the damaged object must be a player.
    """

    source_only: bool = False
    combat_only: bool = False
    to_player_only: bool = False
    looks_back_in_time = False

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event represents matching damage.
        """
        from ...game_state import Player

        return (
            event.key == "damage_dealt"
            and event.payload.get("amount", 0) > 0
            and (
                not self.combat_only
                or event.payload.get("combat", False)
            )
            and (
                not self.to_player_only
                or isinstance(
                    event.payload.get("target_object"),
                    Player,
                )
            )
        )

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the damage condition for the bound trigger source.
        """
        return self.matches(state, trigger.event) and (
            not self.source_only
            or trigger.event.source is trigger.source
        )