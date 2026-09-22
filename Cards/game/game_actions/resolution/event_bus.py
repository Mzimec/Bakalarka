"""Event-time trigger capture, event publication and APNAP trigger handling."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping
from collections.abc import Iterable

if TYPE_CHECKING:
    from ...game_state import State, Player
    from ..data_structs.ability import TriggerAbility


@dataclass(frozen=True)
class CardLastKnownInformation:
    """!
    @brief Snapshot of card characteristics needed by zone-change triggers.

    Stores the relevant pre-event characteristics so triggers can inspect
    an object as it existed before leaving its previous zone.

    @var card
        Runtime card object the snapshot belongs to.
    @var controller
        Controller of the card at snapshot time.
    @var zone
        Zone occupied by the card at snapshot time.
    @var types
        Card types at snapshot time.
    @var zone_revision
        Zone revision used to distinguish different incarnations of the
        same runtime card.
    @var name
        Evaluated name, including a supported enters-as-copy definition.
    @var colors
        Frozen evaluated colors before departure.
    @var keywords
        Frozen evaluated keywords used by damage and other resolving effects.
    @var abilities
        Tuple of the immutable activated/casting definitions present at capture.
    @var triggers
        Tuple of the immutable trigger definitions present at capture.
    @var power
        Evaluated power before mutation, including continuous effects.
    @var toughness
        Evaluated toughness before mutation, including continuous effects.
    @var counters
        Immutable copy of the old incarnation's counters.
    @var subtypes
        Evaluated subtypes before mutation.
    """

    card: Any
    controller: Any
    zone: Any
    types: frozenset
    zone_revision: int
    name: str = ""
    colors: frozenset = frozenset()
    keywords: frozenset = frozenset()
    abilities: tuple = ()
    triggers: tuple = ()
    power: int | None = None
    toughness: int | None = None
    counters: Mapping = field(default_factory=dict)
    subtypes: frozenset = frozenset()

    def __post_init__(self):
        """!
        @brief Freeze incarnation-local values before the live card is reset.

        A copied mapping is essential: the live counter collection is cleared
        on a zone change and must never rewrite an already captured snapshot.
        """
        object.__setattr__(self, "counters", MappingProxyType(dict(self.counters)))
        object.__setattr__(self, "types", frozenset(self.types))
        object.__setattr__(self, "subtypes", frozenset(self.subtypes))


@dataclass(frozen=True)
class GameEvent:
    """!
    @brief Immutable game event with triggers captured at event time.

    `triggered_abilities` stores the triggers detected when the event
    occurred rather than recomputing them later during dispatch.

    @var key
        Event type identifier.
    @var source
        Runtime object that produced or underwent the event.
    @var controller
        Controller associated with the event.
    @var payload
        Immutable event-specific data.
    @var triggered_abilities
        Trigger abilities captured for this event, or `None` before capture.
    """

    key: str
    source: Any | None = None
    controller: Any | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    triggered_abilities: tuple | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        """!
        @brief Freeze the payload mapping after event construction.
        """
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(dict(self.payload)),
        )


def capture_trigger_roster(state):
    """!
    @brief Snapshot all currently available triggered abilities.

    @param state Current game state.
    @return Tuple of triggered abilities visible at capture time.
    """
    getter = getattr(state, "get_triggered_abilities", None)
    return tuple(getter(None)) if getter is not None else ()


def capture_card_information(state, cards=None):
    """!
    @brief Capture last-known information for every card in the game state.

    @param state Current game state.
    @return Mapping from card identity to its current LKI snapshot.
    """
    if cards is None:
        getter = getattr(state, "get_cards", None)

        if getter is None:
            return {}

        cards = getter()

    return {
        id(card): capture_single_card(state, card)
        for card in cards
    }


def capture_single_card(state, card):
    """!
    @brief Snapshot evaluated characteristics without keeping live collections.
    @param state Game whose continuous effects determine these characteristics.
    @param card Object to capture before its incarnation changes.
    @return Immutable last-known information for this incarnation.
    """
    state.refresh_continuous_effects()
    cache = getattr(state, "_card_snapshots", None)
    if cache is not None:
        return cache.capture(state, card, _capture_single_card)
    return _capture_single_card(state, card)


def _capture_single_card(state, card):
    from ...stat_type import STAT_COLORS, STAT_KEYWORDS
    return CardLastKnownInformation(
        card, card.get_controller(state), card.get_zone(),
        frozenset(card.get_types(state)), card.zone_revision,
        name=card.name,
        colors=frozenset(card.get_stat(STAT_COLORS, state)),
        keywords=frozenset(card.get_stat(STAT_KEYWORDS, state)),
        abilities=tuple(card.get_ability_defs(state).values()),
        triggers=tuple(card.get_trigger_defs(state).values()),
        power=card.get_power(state), toughness=card.get_toughness(state),
        counters=card.state.counters, subtypes=frozenset(card.get_subtypes(state)),
    )



def capture_event(state, event, before=None, information=None, after=None):
    """!
    @brief Bind triggered abilities to an event when that event occurs.

    Uses trigger rosters from before and after the mutation so zone-change
    triggers can follow look-back rules. Last-known card information is
    attached to the event and trigger where available.

    Operations may supply shared `before`, `information`, and `after`
    snapshots when several events belong to the same simultaneous batch.

    @param state Current game state after the event's mutation.
    @param event Event whose triggers should be captured.
    @param before Optional trigger roster captured before the mutation.
    @param information Optional last-known card snapshots from before mutation.
    @param after Optional trigger roster captured after the mutation.
    @return Event with `triggered_abilities` populated.
    """
    if event.triggered_abilities is not None:
        return event

    from ..data_structs.ability import TriggerAbility

    # Attach source LKI directly to zone-change events when available.
    if information and id(event.source) in information:
        event = replace(
            event,
            payload={
                **event.payload,
                "last_known": information[id(event.source)],
            },
        )

    references = {}
    def remember(value):
        if hasattr(value, "zone_revision"):
            references[id(value)] = value.zone_revision
        elif isinstance(value, (tuple, list)):
            for item in value:
                remember(item)
    remember(event.source)
    for value in event.payload.values():
        remember(value)
    event = replace(event, payload={**event.payload, "object_revisions": MappingProxyType(references)})

    after = capture_trigger_roster(state) if after is None else after
    before = after if before is None else before

    origin = event.payload.get("from")
    destination = event.payload.get("to")

    # Leaving-the-battlefield events normally inspect the trigger roster
    # that existed before the zone change.
    leaves = event.key in {
        "dies",
        "permanent_died",
        "left_battlefield",
    } or (
        event.key == "card_moved"
        and getattr(origin, "name", origin) == "BATTLEFIELD"
        and getattr(destination, "name", destination) != "BATTLEFIELD"
    )

    triggers = []

    # Evaluate look-back triggers against the pre-event roster and ordinary
    # triggers against the post-event roster.
    for roster, is_before in ((before, True), (after, False)):
        for ability in roster:
            definition = ability.definition
            look_back = getattr(
                definition.condition,
                "looks_back_in_time",
                None,
            )

            use_before = leaves if look_back is None else look_back

            if use_before != is_before:
                continue

            trigger = definition.to_ability(
                ability.source,
                ability.controller,
                event=event,
                source_last_known=(information or {}).get(id(ability.source)),
            )

            # A look-back roster describes the old incarnation even if its
            # card object has already moved. Ordinary ETB triggers instead
            # belong to the new incarnation from the post-event roster.
            previous = (information or {}).get(id(ability.source))
            if is_before and previous is not None:
                trigger.source_revision = previous.zone_revision

            if trigger.matches(state):
                triggers.append(trigger)

    from ..triggers.runtime_triggers import collect_runtime
    triggers.extend(collect_runtime(state, event))
    return replace(
        event,
        triggered_abilities=tuple(triggers),
    )


class EventBus:
    """!
    @brief Publish events and preserve triggers captured at event time.

    Captured triggers remain attached to their originating event even if
    their source later changes zone or otherwise changes characteristics.
    """

    def __init__(self):
        """!
        @brief Create an empty event bus.
        """
        self.emitted_events: list[GameEvent] = []

    def emit(
        self,
        event: GameEvent,
        state: State | None = None,
    ) -> GameEvent:
        """!
        @brief Publish an event and optionally capture its triggers.

        When a game state is supplied, uncaptured events are bound to their
        triggers before publication and also recorded in the current turn's
        event history.

        @param event Event to publish.
        @param state Optional current game state.
        @return The published event, possibly replaced by a captured version.
        """
        if state is not None and event.triggered_abilities is None:
            event = capture_event(state, event)

        self.emitted_events.append(event)

        if state is not None and hasattr(state, "turn"):
            # Reset turn-local event history when entering a new turn.
            if getattr(state, "_history_turn", None) != state.turn.number:
                state._history_turn = state.turn.number
                state.turn_events = []

            state.turn_events.append(event)

        return event

    def collect_trigger_abilities(
        self,
        state: State,
        events: Iterable[GameEvent],
    ) -> list[TriggerAbility]:
        """!
        @brief Collect triggered abilities produced by the supplied events.

        Prefer triggers already captured at event time. Events created by
        legacy or external code without capture fall back to evaluation
        against the current game state.

        @param state Current game state.
        @param events Events whose triggers should be collected.
        @return List of matching trigger abilities.
        """
        triggers = []

        for event in events:
            if event.triggered_abilities is not None:
                triggers.extend(event.triggered_abilities)
            else:
                for ability in state.get_triggered_abilities(event):
                    if ability.matches(state):
                        triggers.append(ability)

        return triggers

    collect_triggered_abilities = collect_trigger_abilities
    collect_trigger_actions = collect_trigger_abilities


class TriggerProcessor:
    """!
    @brief Put pending triggers onto the stack in APNAP order.

    Players are processed starting with the active player and continuing
    in turn order. Each player chooses the ordering and required choices
    for triggers they control before those triggers are placed on the stack.
    """

    def __init__(self, engine=None):
        """!
        @brief Create a trigger processor.

        @param engine Resolution engine used for triggered mana abilities,
               which resolve immediately instead of using the stack.
        """
        self.engine = engine

    def process(
        self,
        state: State,
        triggers: list[TriggerAbility],
    ) -> None:
        """!
        @brief Process pending triggers in active-player/nonactive-player order.

        @param state Current game state.
        @param triggers Pending trigger abilities.
        """
        if not triggers:
            return

        players = list(
            getattr(
                state,
                "active_players",
                getattr(state, "players", ()),
            )
        )

        if not players:
            return

        # Rotate player order so APNAP processing starts with the active player.
        active_position = next(
            (
                i
                for i, player in enumerate(players)
                if player is state.active_player
            ),
            0,
        )

        for player in (
            players[active_position:]
            + players[:active_position]
        ):
            owned = [
                trigger
                for trigger in triggers
                if trigger.controller is player
            ]

            if owned:
                self._decide_triggers(
                    player,
                    state,
                    owned,
                )

        from ..triggers.runtime_triggers import update_queued
        update_queued(state, triggers)

    resolve = process

    def _decide_triggers(
        self,
        controller: Player,
        state: State,
        triggers: Iterable[TriggerAbility],
    ) -> None:
        """!
        @brief Let one controller order and complete choices for its triggers.

        Illegal triggers with no legal action are skipped. Triggered mana
        abilities resolve immediately; all other trigger actions are pushed
        onto the stack.

        @param controller Player controlling the pending triggers.
        @param state Current game state.
        @param triggers Trigger abilities controlled by the player.
        """
        from ...ai.decision_maker import TriggerOrderRequest, AbilityDecisionRequest

        decision_maker = controller.controller
        triggers = tuple(triggers)
        ordered = decision_maker.decide(TriggerOrderRequest(state, controller, triggers)).value.items
        if sorted(map(id, ordered)) != sorted(map(id, triggers)):
            raise ValueError("Trigger order must contain every pending trigger exactly once.")

        for trigger in ordered:
            request = AbilityDecisionRequest(state, controller, trigger)
            choices = tuple(request.options)
            if not choices:
                continue
            action = decision_maker.decide(
                AbilityDecisionRequest(state, controller, trigger, choices)
            ).value
            if not any(action == choice for choice in choices):
                raise ValueError("Choose one of the legal actions for the mandatory trigger.")

            for intent in action.get_intents():
                # Trigger execution should not separately process cost intents.
                if intent.context.is_cost:
                    continue

                if trigger.definition.is_mana_ability:
                    if self.engine is None:
                        raise RuntimeError(
                            "Triggered mana abilities require a resolution engine."
                        )

                    # Triggered mana abilities resolve immediately and do not
                    # use the stack.
                    self.engine.resolve(
                        state,
                        intent,
                    )
                else:
                    state.stack.push(
                        intent.to_stack_item()
                    )


TriggerResolver = TriggerProcessor

