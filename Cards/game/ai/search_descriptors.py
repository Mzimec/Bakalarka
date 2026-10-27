"""Detached choices for replay; concrete actions are regenerated in each world.

Rule keys are local to one fixed scenario/catalog. These are replay addresses,
not equivalence proofs and not a portable saved-game format across rule changes.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

from game.game_state import Card, Player, State
from game.game_actions.data_structs.ability import AbilityDefinition
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.event_bus import GameEvent
from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition
from game.target.target_resolver import RepetitionTargetSlotWrapper


class UnsupportedDescriptor(TypeError):
    """!
    @brief An extension needs an explicit stable replay encoding.
    """


def type_key(value):
    """!
    @brief Identify a descriptor type by module and qualified class name.
    """
    return f"{type(value).__module__}.{type(value).__qualname__}"


def freeze(value, _active=None):
    """!
    @brief Copy decision data to primitive tuples, never retain a live reference.

    Definitions/effects are addressed by their authored key in the fixed catalog.
    Runtime cards use registration IDs and zone incarnations, not random UUIDs.
    Unknown callbacks, cycles and opaque objects fail explicitly.
    """
    if value is None or type(value) in (bool, int, str, float):
        return (type(value).__name__, value)
    if isinstance(value, Enum):
        return ("enum", type_key(value), value.name)
    if isinstance(value, Card):
        return ("card", value.command_id, value.zone_revision, value.owner.idx)
    if isinstance(value, Player):
        return ("player", value.idx)
    if isinstance(value, State):
        # ManaSolverResult.future_state binds to the newly generated world.
        return ("state",)
    if isinstance(value, (AbilityDefinition, ReplacementEffectDefinition, Effect)):
        return ("rule", type_key(value), value.key)
    if isinstance(value, RepetitionTargetSlotWrapper):
        return ("slot", value.slot.key, freeze(value.runtime_key))
    active = set() if _active is None else _active
    if id(value) in active:
        raise UnsupportedDescriptor(f"Cyclic decision data: {type_key(value)}")
    active.add(id(value))
    try:
        def encode(item):
            return freeze(item, active)

        if isinstance(value, Mapping):
            return ("mapping", tuple(sorted(((encode(k), encode(v)) for k, v in value.items()), key=repr)))
        if isinstance(value, (tuple, list)):
            return ("sequence", tuple(encode(v) for v in value))
        if isinstance(value, (set, frozenset)):
            return ("set", tuple(sorted((encode(v) for v in value), key=repr)))
        if isinstance(value, GameEvent):
            # Object-revision keys are Python id() values, not replay identities.
            return ("event", value.key, encode(value.source), encode(value.controller),
                    encode({k: v for k, v in value.payload.items() if k != "object_revisions"}))
        if isinstance(value, Operation):
            from game.rules.permanents import TokenEntryOperation
            if type(value) is TokenEntryOperation:
                # These cards have no registration ID until entry. Native
                # creation order is stable within the simultaneous batch.
                return (type_key(value), tuple((k,
                    ("pending_token", value.creation_index, value.card.owner.idx, value.card.definition.name)
                    if k == "card" else encode(v)) for k, v in sorted(vars(value).items())))
            return (type_key(value), tuple((k, encode(v)) for k, v in sorted(vars(value).items())))
        # Trigger abilities and bound replacement effects are mutable runtime
        # records, unlike their immutable definitions.
        from game.game_actions.data_structs.ability import Ability
        from game.game_actions.generation.decision_abstraction.priority_frames import PrioritySourceOption
        from game.game_actions.generation.decision_abstraction.ability_frames import AbilityConstructionOption
        if type(value) is AbilityConstructionOption:
            return (type_key(value), value.stage, encode(value.choice), encode(value.witness))
        if type(value) is PrioritySourceOption:
            # Generation policies contain runtime strategies/callbacks. They
            # are never part of the portable source identity or replay address.
            base = (type_key(value), encode(value.ability), encode(value.witness))
            return base + (("construction_version", 2),) if value._construction is not None else base
        from game.game_actions.resolution.replacement_effects import ReplacementEffect
        if isinstance(value, Ability):
            return (type_key(value), encode(value.definition), encode(value.source), encode(value.controller),
                    encode(getattr(value, "source_revision", None)), encode(getattr(value, "event", None)),
                    encode(getattr(value, "source_last_known", None)))
        if isinstance(value, ReplacementEffect):
            return (type_key(value), tuple((k, encode(v)) for k, v in sorted(vars(value).items())))
        # Runtime Ability inherits an empty dataclass through its stat mixins.
        # Handle explicit runtime adapters first; inherited fields alone would
        # erase the source/event and collapse distinct trigger orders.
        if is_dataclass(value) and not isinstance(value, type):
            return (type_key(value), tuple((f.name, encode(getattr(value, f.name))) for f in fields(value)))
        raise UnsupportedDescriptor(f"No replay encoding for {type_key(value)}")
    finally:
        active.remove(id(value))


@dataclass(frozen=True)
class ChoiceDescriptor:
    """!
    @brief Stable native replay identity represented by detached primitive tuples.
    @var kind Explicit record/action kind used by this protocol.
    @var data Data retained by this record.
    """
    kind: str
    data: tuple

    @classmethod
    def from_option(cls, option):
        """!
        @brief Create a stable replay descriptor for a supported complete native option.
        """
        return cls(type(option).__name__, freeze(option))
