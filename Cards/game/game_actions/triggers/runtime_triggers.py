"""Runtime trigger lifetimes shared by delayed, reflexive and state abilities."""

from dataclasses import dataclass

from ..data_structs.operation import Operation
from ..data_structs.ability import TriggerAbility
from ..resolution.event_bus import GameEvent, capture_single_card


@dataclass
class RuntimeTrigger:
    """!
    @brief One registered ability and its game-owned lifetime state.

    Ownership and source identity are fixed at creation. A delayed trigger
    survives its source leaving; a state trigger may supply an explicit expiry.
    """

    definition: object
    source: object
    controller: object
    information: object
    once: bool = True
    expires: object = None
    predicate: object = None
    consumed: bool = False
    pending: bool = False
    queued: bool = False
    resolving: bool = False

    def bind(self, event):
        """! @brief Bind a trigger without replacing its captured source identity. """
        trigger = TriggerAbility(
            self.definition, self.source, self.controller, event, self.information
        )
        trigger.source_revision = self.information.zone_revision
        trigger.registration = self
        return trigger


def registrations(state):
    """! @brief Lazily create runtime registrations inside rollback-owned state. """
    if not hasattr(state, "runtime_triggers"):
        state.runtime_triggers = []
    return state.runtime_triggers


def register_trigger(state, context, definition, *, once=True, expires=None, predicate=None):
    """!
    @brief Register a delayed trigger or a persistent state-trigger predicate.
    @param state Owning game state, including transaction rollback.
    @param context Creating effect's source and controller.
    @param definition Existing TriggerAbilityDefinition used for effects and choices.
    @param once Whether the first matching event consumes a delayed registration.
    @param expires Optional pure callable `(state) -> bool` ending its lifetime.
    @param predicate Optional pure state condition; pending instances suppress repeats.
    @return The independently owned runtime registration.
    """
    if context.source is None or context.controller is None:
        raise ValueError("Runtime triggers need a source and controller.")
    if predicate is not None and definition.is_mana_ability:
        raise ValueError("A state trigger cannot be a triggered mana ability.")
    info = context.source_information(state) or capture_single_card(state, context.source)
    entry = RuntimeTrigger(definition, context.source, context.controller, info,
                           once, expires, predicate)
    registrations(state).append(entry)
    return entry


def collect_runtime(state, event, *, states_only=False):
    """!
    @brief Capture runtime triggers once at the same boundary as printed triggers.
    @return Bound triggers whose ordering/choices still use TriggerProcessor.
    """
    result = []
    entries = getattr(state, "runtime_triggers", ())
    for entry in tuple(entries):
        if entry.consumed:
            continue
        if entry.expires is not None and entry.expires(state):
            entry.consumed = True
            continue
        if entry.controller not in state.active_players:
            continue
        if entry.predicate is not None:
            on_stack = any(
                item.action_resolution.context.trigger_registration is entry
                for item in state.stack.items
            )
            if entry.queued and not on_stack and not entry.resolving:
                entry.pending = entry.queued = False
            if entry.pending or not entry.predicate(state):
                continue
        elif states_only:
            continue
        trigger = entry.bind(event)
        if entry.predicate is None and not trigger.matches(state):
            continue
        if entry.definition.intervening_if is not None and not entry.definition.intervening_if(state, event):
            continue
        if entry.predicate is not None:
            entry.pending = True
        elif entry.once:
            entry.consumed = True
        result.append(trigger)
    return result


def update_queued(state, triggers=()):
    """!
    @brief Remember which processed state triggers actually reached the stack.

    Only registrations from the just-processed trigger batch are updated. This
    avoids clearing a different pending state trigger while a nested mana
    trigger is being resolved during cost payment.

    @param state Current game state.
    @param triggers Trigger abilities that were offered to `TriggerProcessor`.
    """
    entries = []
    seen = set()

    for trigger in triggers:
        entry = getattr(trigger, "registration", None)

        if entry is not None and id(entry) not in seen:
            entries.append(entry)
            seen.add(id(entry))

    for entry in entries:
        if entry.predicate is None or not entry.pending or entry.resolving:
            continue

        entry.queued = any(
            item.action_resolution.context.trigger_registration is entry
            for item in state.stack.items
        )

        if not entry.queued:
            entry.pending = False


class RegisterTriggerOperation(Operation):
    """! @brief Create a delayed/state registration as a normal resolving operation. """

    def __init__(self, context, definition, **settings):
        super().__init__(context)
        self.definition, self.settings = definition, settings

    def execute(self, state):
        register_trigger(state, self.context, self.definition, **self.settings)
        return []


class ReflexiveTriggerOperation(Operation):
    """!
    @brief Check a reflexive trigger against the actual event that caused creation.

    The caller supplies the completed instruction's event, not an attempted
    choice. The resulting ability waits for the enclosing resolution to finish.
    """

    def __init__(self, context, definition, event):
        super().__init__(context)
        self.definition, self.event = definition, event

    def execute(self, state):
        info = self.context.source_information(state)
        entry = RuntimeTrigger(self.definition, self.context.source, self.context.controller, info)
        trigger = entry.bind(self.event)
        if not trigger.matches(state):
            return []
        return [GameEvent("reflexive_trigger_created", self.context.source,
                          self.context.controller, triggered_abilities=(trigger,))]
