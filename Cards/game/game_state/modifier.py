"""Continuous effects, duration management and characteristic modifiers."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar, Generic
from dataclasses import dataclass, field
from functools import total_ordering
from collections.abc import Set

if TYPE_CHECKING:
    from .state import State
    from .card import Card
    from ..target import TargetSpec
    from helper import Heap
    from ..game_actions import GameEvent
    from .stat import HasModifiers

from ..mana.mana_value import ManaValue
from helper import Heap
from helper.runtime_object import RuntimeObject, KeyedCollection, KeyedObject
from ..enums import *
from ..stat_type import *


@total_ordering
@dataclass(frozen=True)
class TimeStamp:
    """!
    @brief Global turn/phase timestamp used for effect ordering and expiration.
    """

    turn_number: int
    phase: TurnPhase

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, TimeStamp)
            and self.turn_number == other.turn_number
            and self.phase == other.phase
        )

    def __ge__(self, other: TimeStamp) -> bool:
        return self.turn_number > other.turn_number or (
            self.turn_number == other.turn_number and self.phase.value >= other.phase.value
        )


@dataclass(frozen=True)
class GameMoment:
    """!
    @brief Player-relative point in the game used by player-based durations.
    """

    player_idx: int
    moment: PlayerMoment

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, GameMoment)
            and self.player_idx == other.player_idx
            and self.moment == other.moment
        )


@total_ordering
@dataclass(frozen=True)
class PlayerMoment:
    """!
    @brief Moment measured relative to one player's completed turns.
    """

    taken_turns: int
    phase: TurnPhase

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, PlayerMoment)
            and self.taken_turns == other.taken_turns
            and self.phase == other.phase
        )

    def __ge__(self, other: PlayerMoment) -> bool:
        return self.taken_turns > other.taken_turns or (
            self.taken_turns == other.taken_turns and self.phase >= other.phase
        )


class Duration(ABC):
    """!
    @brief Interface describing when a continuous effect expires.
    """

    @abstractmethod
    def is_over(self, state: State) -> bool:
        """!
        @brief Check whether this duration has ended.

        @param state Current game state.
        @return True if the associated effect should expire.
        """
        pass


@dataclass(frozen=True)
class TimeStampDuration(Duration):
    """!
    @brief Duration ending at a global turn/phase timestamp.
    """

    end_time_stamp: TimeStamp

    @classmethod
    def create(
        cls, state: State, number: int = 1, phase: TurnPhase = TurnPhase.END_STEP
    ) -> TimeStampDuration:
        """!
        @brief Create a duration ending after the requested number of occurrences
        of a phase.

        If the target phase has not yet occurred this turn, the current turn
        counts as the first occurrence.

        @param state Current game state.
        @param number Number of target phase occurrences to wait.
        @param phase Phase at which the duration expires.
        @return Constructed timestamp duration.
        """
        created_at: TimeStamp = state.time_stamp
        end_turn_dif = number if phase.value < created_at.phase.value else number - 1

        end_at = TimeStamp(
            phase=phase,
            turn_number=created_at.turn_number + end_turn_dif,
        )

        return cls(end_time_stamp=end_at)

    def is_over(self, state: State) -> bool:
        """!
        @brief Check whether the configured end timestamp has been reached.
        """
        return self.end_time_stamp <= state.time_stamp


@dataclass(frozen=True)
class GameMomentDuration(Duration):
    """!
    @brief Duration ending at a moment relative to one player.
    """

    end_game_moment: GameMoment

    def is_over(self, state: State) -> bool:
        """!
        @brief Check whether the tracked player has reached the end moment.

        The duration also ends if the referenced player no longer represents a
        valid living player in the game.
        """
        if (
            self.end_game_moment.player_idx >= len(state.players)
            or self.end_game_moment.player_idx < 0
            or not state.players[self.end_game_moment.player_idx].is_alive
        ):
            return True

        player_moment = state.players[self.end_game_moment.player_idx].moment
        return player_moment >= self.end_game_moment.moment


class PermanentDuration(Duration):
    """!
    @brief Duration that never expires on its own.
    """

    def is_over(self, state: State) -> bool:
        """!
        @brief Permanent durations never end automatically.
        """
        return False


@dataclass(frozen=True)
class AnchoredDuration(Duration):
    """!
    @brief Duration tied to an anchor remaining in one of a set of zones.
    """

    zone: frozenset[ZoneType]
    anchor: Any

    def is_over(self, state: State) -> bool:
        """!
        @brief End when the anchor disappears or leaves all allowed zones.
        """
        if self.anchor is None:
            return True
        return self.anchor.get_zone() not in self.zone


class TargetingStrategy(ABC):
    """!
    @brief Strategy for determining objects affected by a continuous effect.
    """

    @abstractmethod
    def get_affected(self, source: Card, state: State) -> list[Any]: ...

    @abstractmethod
    def on_event(self, event: GameEvent, source: Card, state: State) -> list[Any] | None: ...


@dataclass(frozen=True)
class StaticTagetingStrategy(TargetingStrategy):
    """!
    @brief Targeting strategy whose affected set is queried directly from a spec.
    """

    target_spec: TargetSpec

    def get_affected(self, source, state):
        """!
        @brief Return objects currently matched by the target specification.
        """
        return list(
            self.target_spec.generate_candidates(source, source.get_controller(state), state)
        )

    def on_event(self, event, source, state):
        """!
        @brief Static targeting does not request an event-driven recomputation.
        """
        return None


@dataclass(frozen=True)
class DynamicTargetingStrategy(TargetingStrategy):
    """!
    @brief Targeting strategy that can recompute membership after game events.
    """

    target_spec: TargetSpec

    def get_affected(self, source, state):
        """!
        @brief Return objects currently matched by the target specification.
        """
        return list(
            self.target_spec.generate_candidates(source, source.get_controller(state), state)
        )

    def on_event(self, event: GameEvent, source: Card, state: State) -> list[Any] | None:
        """!
        @brief Recompute affected objects after a relevant event.

        Current implementation conservatively recomputes after every GameEvent.
        """
        # TODO Create classes for relevant GameEvents
        if isinstance(event, (GameEvent)):
            return self.get_affected(source, state)
        return None


@dataclass(frozen=True)
class ContinuousEffectDefinition:
    """!
    @brief Immutable definition of one continuous effect.

    @var duration
        Lifetime policy of the effect.
    @var source
        Runtime source supplying the effect.
    @var created_at
        Timestamp used for ordering effects with equal dependency priority.
    @var targeting
        Strategy defining the affected objects.
    @var modifiers
        Characteristic modifiers supplied by this effect.
    @var depends_on
        Explicit continuous-effect dependency keys.
    @var static_ability_key
        Static ability that generated the effect, if any.
    @var characteristic_defining
        Whether the effect is characteristic-defining.
    """

    duration: Duration
    source: Card
    created_at: TimeStamp
    targeting: TargetingStrategy
    modifiers: dict[StatType, list[Modifier]]
    depends_on: frozenset[str] = frozenset()
    static_ability_key: str | None = None
    characteristic_defining: bool = False


@dataclass
class ContinuousEffectState:
    """!
    @brief Mutable runtime state of a continuous effect.

    `currently_affected` tracks registered memberships, while `active_layers`
    limits which modifiers are currently exposed during layered evaluation.
    """

    currently_affected: set[HasModifiers]
    active_layers: set[Layer] | None = None
    timestamp_order: int = 0
    inferred_dependencies: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ContinuousEffect(RuntimeObject):
    """!
    @brief Runtime continuous effect bound to a definition and mutable state.
    """

    _key: str
    definition: ContinuousEffectDefinition
    state: ContinuousEffectState

    @property
    def key(self) -> str:
        return self._key

    @property
    def modifiers(self) -> dict[StatType, list[Modifier]]:
        return self.definition.modifiers

    @property
    def duration(self) -> Duration:
        return self.definition.duration

    def is_over(self, state: State) -> bool:
        """!
        @brief Check whether this effect's duration has ended.
        """
        return self.definition.duration.is_over(state)

    def get_affected(self, state: State) -> list[HasModifiers]:
        """!
        @brief Return objects currently affected by this effect.

        Entry projections remap real card identities onto projected clones.
        Static abilities belonging to the projected entrant are additionally
        restricted to the entrant itself where required by entry evaluation.
        """
        targets = self.definition.targeting.get_affected(self.definition.source, state)

        projected = getattr(state, "_projected_cards", None)
        if projected is not None:
            targets = [projected[card.key] for card in targets if card.key in projected]

            entrant = getattr(state, "_projected_entrant", None)
            if self.definition.static_ability_key is not None and self.definition.source is entrant:
                targets = [card for card in targets if card is entrant]

        return targets

    def _register(self, obj: HasModifiers) -> None:
        """!
        @brief Register this effect as active on one runtime object.
        """
        obj.state.active_cont_effects.add(self.key)
        self.state.currently_affected.add(obj)

    def _unregister(self, obj: HasModifiers) -> None:
        """!
        @brief Remove this effect from one runtime object.
        """
        obj.state.active_cont_effects.discard(self.key)
        self.state.currently_affected.discard(obj)

    def attach(self, state: State) -> None:
        """!
        @brief Register the effect on every currently matched object.
        """
        for target in self.get_affected(state):
            self._register(target)

    def detach(self, state: State) -> None:
        """!
        @brief Remove this effect from all currently registered objects.
        """
        for target in tuple(self.state.currently_affected):
            self._unregister(target)

    def on_event(self, event: GameEvent, state: State) -> None:
        """!
        @brief Update effect membership after an event.

        @param event Event passed to the targeting strategy.
        @param state Current game state.
        """
        new_affected_list = self.definition.targeting.on_event(event, self.definition.source, state)
        if new_affected_list is None:
            return

        new_affected = set(new_affected_list)
        added = new_affected - self.state.currently_affected
        removed = self.state.currently_affected - new_affected

        for target in added:
            self._register(target)

        for target in removed:
            self._unregister(target)


T = TypeVar("T")


class Modifier(Generic[T], ABC):
    """!
    @brief Base interface for a characteristic transformation.
    """

    @property
    @abstractmethod
    def layer(self) -> Layer: ...

    @property
    @abstractmethod
    def behavior(self) -> ModifierType: ...

    @abstractmethod
    def modify(self, original: T) -> T: ...


class AddModifier(Generic[T], Modifier[T], ABC):
    """!
    @brief Base class for additive modifiers.
    """

    @property
    def behavior(self):
        return ModifierType.ADD

    @property
    def layer(self):
        return Layer.ADD


@dataclass(frozen=True)
class SetModifier(Modifier[T]):
    """!
    @brief Replace a characteristic with a configured value.
    """

    @property
    def behavior(self):
        return ModifierType.SET

    value: T

    @property
    def layer(self):
        return Layer.SET

    def modify(self, original: T) -> T:
        """!
        @brief Replace the current characteristic value.

        Container-like runtime values exposing `to_immutable` are reconstructed
        through their own type instead of reusing the configured object.
        """
        if hasattr(original, "to_immutable"):
            return type(original)(self.value)
        return self.value


@dataclass(frozen=True)
class AddIntModifier(AddModifier[int]):
    """!
    @brief Add a fixed integer to a numeric characteristic.
    """

    value: int

    def modify(self, original: int) -> int:
        """!
        @brief Add the configured value to the original integer.
        """
        return original + self.value


@dataclass(frozen=True)
class MultiplyIntModifier(Modifier[int]):
    """!
    @brief Multiply a numeric characteristic by a fixed integer.
    """

    @property
    def behavior(self):
        return ModifierType.MULTIPLY

    value: int

    @property
    def layer(self):
        return Layer.MULTIPLY

    def modify(self, original: int) -> int:
        """!
        @brief Multiply the original value by the configured factor.
        """
        return original * self.value


@dataclass(frozen=True)
class AddSetModifier(Generic[T], AddModifier[T]):
    """!
    @brief Add values to a set-valued characteristic.
    """

    value: frozenset[T]

    def modify(self, original: set[T]) -> set[T]:
        """!
        @brief Add configured values to the supplied set.
        """
        original.update(self.value)
        return original


@dataclass(frozen=True)
class RemoveSetModifier(Generic[T], AddModifier[set[T]]):
    """!
    @brief Remove values from a set-valued characteristic.
    """

    value: frozenset[T]

    def modify(self, original: set[T]) -> set[T]:
        """!
        @brief Remove configured values from the supplied set.
        """
        original.difference_update(self.value)
        return original


KT = TypeVar("KT", bound=KeyedObject)


@dataclass(frozen=True)
class AddCollectionModifier(Generic[KT], AddModifier[KeyedCollection[KT]]):
    """!
    @brief Add keyed runtime objects to a keyed collection.
    """

    value: list[KT]

    def modify(self, original: KeyedCollection[KT]) -> KeyedCollection[KT]:
        """!
        @brief Add objects whose keys are not already present.
        """
        for o in self.value:
            if o.key in original:
                continue
            original[o.key] = o

        return original


@dataclass(frozen=True)
class AddManaCostModifier(AddModifier[ManaValue]):
    """!
    @brief Add generic or symbolic mana to a spell's casting cost.
    """

    value: ManaValue | int

    def modify(self, value: ManaValue) -> ManaValue:
        """!
        @brief Return a fresh mana value with this modifier applied.

        The original printed/current cost remains unchanged. Integer values are
        interpreted as generic mana additions.
        """
        from ..mana.mana_value import ManaValue as MutableManaValue, GenericSymbol

        result = MutableManaValue(value)

        if isinstance(self.value, int):
            result.add_pair(GenericSymbol(), self.value)
        else:
            result.add(MutableManaValue(self.value))

        return result


@dataclass
class TimeStampedModifier:
    """!
    @brief Modifier paired with ordering and dependency metadata.

    @var modifier
        Characteristic modifier to apply.
    @var time_stamp
        Primary timestamp ordering key.
    @var order
        Stable secondary creation/attachment order.
    @var effect_key
        Continuous-effect key supplying this modifier.
    @var depends_on
        Dependency keys relevant to this modifier's layer.
    """

    modifier: Modifier
    time_stamp: TimeStamp
    order: int = 0
    effect_key: str = ""
    depends_on: frozenset[str] = frozenset()


class ContinuousEffectsManager:
    """!
    @brief Own live continuous effects and duration-related lookup structures.
    """

    def __init__(self) -> None:
        self._continuous_effects: dict[str, ContinuousEffect] = dict()
        self._sequence = 0

        self._time_stamp_heap: Heap[TimeStamp] = Heap()
        self._time_stamp_bucket: dict[TimeStamp, list[str]] = dict()

        self._moment_heap_map: dict[int, Heap[PlayerMoment]] = dict()
        self._moment_buckets: dict[int, dict[PlayerMoment, list[str]]] = dict()

    def next_order(self):
        """!
        @brief Allocate the next stable runtime ordering number.
        """
        self._sequence += 1
        return self._sequence

    def get(self, key: str) -> ContinuousEffect | None:
        """!
        @brief Return a continuous effect by key, if present.
        """
        return self._continuous_effects.get(key)

    def add(self, ce: ContinuousEffect) -> None:
        """!
        @brief Register a continuous effect and index its expiration point.

        @throws ValueError If another live effect already uses the same key.
        """
        if ce.key in self._continuous_effects:
            raise ValueError(f"  Duplicate key '{ce.key}' in _continuous_effects.")

        ce.state.timestamp_order = self.next_order()
        self._continuous_effects[ce.key] = ce

        if isinstance(ce.duration, TimeStampDuration):
            time_stamp = ce.duration.end_time_stamp
            self._time_stamp_heap.push(time_stamp)

            if time_stamp in self._time_stamp_bucket:
                self._time_stamp_bucket[time_stamp].append(ce.key)
            else:
                self._time_stamp_bucket[time_stamp] = [ce.key]

        if isinstance(ce.duration, GameMomentDuration):
            game_moment = ce.duration.end_game_moment

            if game_moment.player_idx not in self._moment_heap_map:
                self._moment_heap_map[game_moment.player_idx] = Heap()

            self._moment_heap_map[game_moment.player_idx].push(game_moment.moment)

            if game_moment.moment in self._moment_buckets[game_moment.player_idx]:
                self._moment_buckets[game_moment.player_idx][game_moment.moment].append(ce.key)
            else:
                self._moment_buckets[game_moment.player_idx][game_moment.moment] = [ce.key]

    def pop(self, key: str) -> ContinuousEffect:
        """!
        @brief Remove an effect and its duration-index entries.

        @param key Continuous-effect key.
        @return Removed effect.
        """
        ce = self._continuous_effects.pop(key)

        if isinstance(ce.duration, TimeStampDuration):
            time_stamp = ce.duration.end_time_stamp
            self._time_stamp_heap.remove(time_stamp)

            if time_stamp in self._time_stamp_bucket:
                if key in self._time_stamp_bucket[time_stamp]:
                    self._time_stamp_bucket[time_stamp].remove(key)

        if isinstance(ce.duration, GameMomentDuration):
            player_idx = ce.duration.end_game_moment.player_idx
            moment = ce.duration.end_game_moment.moment

            if player_idx in self._moment_heap_map:
                self._moment_heap_map[player_idx].remove(moment)

            if player_idx in self._moment_buckets and moment in self._moment_buckets[player_idx]:
                if key in self._moment_buckets[player_idx][moment]:
                    self._moment_buckets[player_idx][moment].remove(key)

                if not self._moment_buckets[player_idx][moment]:
                    self._moment_buckets[player_idx].pop(moment)

        return ce

    def clean_up(self, state: State) -> None:
        """!
        @brief Remove effects whose indexed time-based durations have expired.

        Timestamp durations are checked globally. Player-moment durations are
        checked for the currently active player.
        """
        while self._time_stamp_heap and self._time_stamp_heap.peek() <= state.time_stamp:
            time_stamp = self._time_stamp_heap.pop()
            keys = self._time_stamp_bucket.pop(time_stamp)

            for key in keys:
                self._continuous_effects.pop(key, None)

        active_player_idx = state.active_player_idx
        active_heap = self._moment_heap_map.get(active_player_idx)

        if active_heap:
            while (
                self._moment_heap_map[active_player_idx]
                and self._moment_heap_map[active_player_idx].peek() <= state.player_moment
            ):
                player_moment = active_heap.pop()
                player_dict = self._moment_buckets.get(active_player_idx, {})
                keys = player_dict.pop(player_moment, [])

                for key in keys:
                    self._continuous_effects.pop(key, None)


class Counter(ABC):
    """!
    @brief Interface mapping one counter type to characteristic modifiers.
    """

    @abstractmethod
    def get_modifiers(self, amount: int) -> list[Modifier]: ...


class MinusCounter(Counter):
    """!
    @brief Runtime modifier behavior for -1/-1 counters.
    """

    def get_modifiers(self, amount):
        """!
        @brief Return power/toughness modifiers for the requested amount.
        """
        return {
            STAT_POWER: [AddIntModifier(-amount)],
            STAT_TOUGHNESS: [AddIntModifier(-amount)],
        }


class PlusCounter(Counter):
    """!
    @brief Runtime modifier behavior for +1/+1 counters.
    """

    def get_modifiers(self, amount):
        """!
        @brief Return power/toughness modifiers for the requested amount.
        """
        return {
            STAT_POWER: [AddIntModifier(amount)],
            STAT_TOUGHNESS: [AddIntModifier(amount)],
        }


STAT_TO_COUNTERS: dict[StatType, frozenset[CounterType]] = {
    STAT_POWER: {CounterType.PLUS_ONE, CounterType.MINUS_ONE},
    STAT_TOUGHNESS: {CounterType.PLUS_ONE, CounterType.MINUS_ONE},
}

TYPE_TO_COUNTER: dict[CounterType, Counter] = {
    CounterType.PLUS_ONE: PlusCounter(),
    CounterType.MINUS_ONE: MinusCounter(),
}


class ModifierSource(ABC):
    """!
    @brief Interface exposing modifiers contributed by one runtime source type.
    """

    @abstractmethod
    def get_modifiers(self, stat: StatType, state: State) -> dict[TimeStampedModifier]: ...


@dataclass
class ContinuouosEffectModifierSource(ModifierSource):
    """!
    @brief Modifier source backed by continuous-effect memberships.
    """

    active_cont_effects: set[str] = field(default_factory=set)

    def try_register_cont_effect(self, key: str) -> bool:
        """!
        @brief Add an effect key if it is not already active.
        """
        if key in self.active_cont_effects:
            return False

        self.active_cont_effects.add(key)
        return True

    def try_unregister_cont_effect(self, key: str) -> bool:
        """!
        @brief Remove an effect key if currently active.
        """
        if key not in self.active_cont_effects:
            return False

        self.active_cont_effects.remove(key)
        return True

    def _get_cont_effect(self, key: str, state: State) -> ContinuousEffect | None:
        """!
        @brief Resolve a registered effect key through the game state.
        """
        return state.get_cont_effect(key)

    def get_modifiers(self, stat, state):
        """!
        @brief Collect active continuous-effect modifiers for one characteristic.

        During layered evaluation, modifiers whose layer is not yet active are
        hidden. Dependency metadata prefers the resolved dependency order
        inferred for the modifier's layer.
        """
        modifiers: list[TimeStampedModifier] = []
        invalid_keys: list[str] = []

        for key in self.active_cont_effects:
            ce = self._get_cont_effect(key, state)
            if ce is None:
                invalid_keys.append(key)
                continue

            sm = ce.modifiers.get(stat)
            if sm is None:
                continue

            for mod in sm:
                from .layers import modifier_layer

                if (
                    ce.state.active_layers is not None
                    and modifier_layer(stat, mod) not in ce.state.active_layers
                ):
                    continue

                # Context-sensitive modifiers may bind themselves to the effect
                # source and current state before entering the stat evaluator.
                if hasattr(mod, "for_context"):
                    mod = mod.for_context(ce.definition.source, state)

                modifiers.append(
                    TimeStampedModifier(
                        mod,
                        ce.definition.created_at,
                        ce.state.timestamp_order,
                        ce.key,
                        ce.state.inferred_dependencies.get(
                            modifier_layer(stat, mod), ce.definition.depends_on
                        ),
                    )
                )

        # Stale memberships can remain if an effect disappeared before this
        # object's local membership set was cleaned.
        for key in invalid_keys:
            self.active_cont_effects.discard(key)

        return modifiers


@dataclass
class CounterModifierSource(ModifierSource):
    """!
    @brief Modifier source derived from counters currently on an object.
    """

    counters: dict[CounterType, int] | None = field(default_factory=dict)

    def get_modifiers(self, stat, state):
        """!
        @brief Collect counter-based modifiers affecting one characteristic.
        """
        modifiers: list[TimeStampedModifier] = []

        stat_counters = STAT_TO_COUNTERS.get(stat)

        if not stat_counters:
            return modifiers

        for sc in stat_counters:
            if sc not in self.counters or sc not in TYPE_TO_COUNTER:
                continue

            counter = TYPE_TO_COUNTER[sc]
            stat_modifiers = counter.get_modifiers(self.counters[sc]).get(stat)
            if stat_modifiers is None:
                continue

            for mod in stat_modifiers:
                modifiers.append(TimeStampedModifier(mod, state.time_stamp))

        return modifiers


@dataclass
class Attachable(RuntimeObject, ABC):
    """!
    @brief Runtime object that can attach to a card and supply modifiers to it.
    """

    @property
    @abstractmethod
    def attached_to(self) -> Card | None: ...

    @attached_to.setter
    @abstractmethod
    def attached_to(self, value: Card | None) -> None: ...

    @property
    @abstractmethod
    def last_attach_at(self) -> TimeStamp | None: ...

    @last_attach_at.setter
    @abstractmethod
    def last_attach_at(self, value: TimeStamp | None) -> None: ...

    @abstractmethod
    def modifiers_to_attach(self, state: State) -> dict[StatType, list[Modifier]] | None: ...

    def attach(self, attach_to, state: State) -> None:
        """!
        @brief Attach this object to a new host.

        Reattaching first detaches from any previous host. Timestamp and
        sequence order are recorded for modifier ordering.
        """
        if self.attached_to is attach_to:
            return

        self.detach()

        attach_to.state.attached[self.key] = self
        self.attached_to = attach_to
        self.last_attach_at = state.time_stamp
        self.attachment_order = state._cont_effect_manager.next_order()

        state.notify_card_changed(attach_to)

    def detach(self) -> None:
        """!
        @brief Detach this object from its current host, if any.
        """
        host = self.attached_to
        if host is None:
            return

        host.state.attached.pop(self.key, None)
        self.attached_to = None
        self.last_attach_at = None

        host._notify_changed()


@dataclass
class AttachedModifierSource(ModifierSource):
    """!
    @brief Modifier source contributed by objects attached to a host.
    """

    attached: dict[str, Attachable] | None = field(default_factory=dict)

    def get_modifiers(self, stat, state):
        """!
        @brief Collect modifiers supplied by all current attachments.

        Attachment timestamp and attachment order provide deterministic ordering
        analogous to continuous-effect timestamp ordering.
        """
        modifiers: list[Modifier] = []

        if not self.attached:
            return modifiers

        for v in self.attached.values():
            mas = v.modifiers_to_attach(state).get(stat)
            if mas is None:
                continue

            for mod in mas:
                modifiers.append(
                    TimeStampedModifier(
                        mod,
                        v.last_attach_at,
                        getattr(v, "attachment_order", 0),
                    )
                )

        return modifiers