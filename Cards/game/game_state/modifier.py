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
from ...helper.runtime_object import RuntimeObject, KeyedCollection, KeyedObject
from ..enums import *
from ..stat_type import *


@total_ordering
@dataclass(frozen=True)
class TimeStamp:

    turn_number: int
    phase: TurnPhase


    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, TimeStamp)
            and self.turn_number == other.turn_number
            and self.phase == other.phase 
        )
    
    def __ge__(self, other: TimeStamp) -> bool:
        return ( 
            self.turn_number > other.turn_number
            or (
                self.turn_number == other.turn_number
                and self.phase >= other.phase
            )
        )
    

@dataclass(frozen=True)
class GameMoment:
    
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

    taken_turns: int
    phase: TurnPhase

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, PlayerMoment)
            and self.taken_turns == other.taken_turns
            and self.phase == other.phase
        )
    
    def __ge__(self, other: PlayerMoment) -> bool:
        return (
            self.taken_turns > other.taken_turns
            or (
                self.taken_turns == other.taken_turns
                and self.phase >= other.phase
            )
        )


class Duration(ABC):
    
    @abstractmethod
    def is_over(self, state: State) -> bool:
        pass


@dataclass(frozen=True)
class TimeStampDuration(Duration):
    
    end_time_stamp: TimeStamp

    @classmethod
    def create(
        cls, 
        state: State,
        number: int = 1, 
        phase: TurnPhase = TurnPhase.END_STEP
    ) -> TimeStampDuration:

        created_at: TimeStamp = state.time_stamp
        end_turn_dif = number if phase < created_at.phase else number - 1
        
        end_at = TimeStamp(
            phase=phase,
            turn_number=created_at.turn_number + end_turn_dif,
        )

        return cls(end_time_stamp=end_at)
    
    
    def is_over(self, state: State) -> bool:
        return self.end_time_stamp <= state.time_stamp


@dataclass(frozen=True)
class GameMomentDuration(Duration):

    end_game_moment: GameMoment

    def is_over(self, state: State) -> bool:
        if (
            self.end_game_moment.player_idx >= len(state.players)
            or self.end_game_moment.player_idx < 0
            or not state.players[self.end_game_moment.player_idx].is_alive
        ):
            return True
        
        player_moment = state.players[self.end_game_moment.player_idx].moment
        return player_moment >= self.end_game_moment.moment


class PermanentDuration(Duration):

    def is_over(self, state: State) -> bool:
        return False


@dataclass(frozen=True)
class AnchoredDuration(Duration):
    
    zone: frozenset[ZoneType]
    anchor: Any

    def is_over(self, state: State) -> bool:
        if self.anchor is None:
            return True
        return False


class TargetingStrategy(ABC):

    @abstractmethod
    def get_affected(self, source: Card, state: State) -> list[Any]:
        ...
    
    @abstractmethod
    def on_event(self, event: GameEvent, source: Card, state: State) -> list [Any] | None:
        ...


@dataclass(frozen=True)
class StaticTagetingStrategy(TargetingStrategy):

    target_spec: TargetSpec

    def get_affected(self, source, state):
        return self.target_spec.get_candidates(source, state)
    
    def on_event(self, event, source, state):
        return None
    

@dataclass(frozen=True)
class DynamicTargetingStrategy(TargetingStrategy):

    target_spec: TargetSpec

    def get_affected(self, source, state):
        return self.target_spec.get_candidates(source, state)

    def on_event(self, event: GameEvent, source: Card, state: State) -> list[Any] | None:
        # Přepočítá při jakémkoliv relevantním eventu
        if isinstance(event, (GameEvent)): #TODO Create classes for relevant GameEvents
            return self.get_affected(source, state)
        return None
    

@dataclass(frozen=True)
class ContinuousEffectDefinition:

    duration: Duration
    source: Card
    created_at: TimeStamp
    targeting: TargetingStrategy
    modifiers: dict[StatType, list[Modifier]]


@dataclass
class ContinuousEffectState:

    currently_affected: set[HasModifiers]


@dataclass(frozen=True)
class ContinuousEffect(RuntimeObject):

    _key: str
    definition: ContinuousEffectDefinition
    state: ContinuousEffectState
    
    @property
    def key(self) -> str:
        self._key

    @property
    def modifiers(self) -> dict[StatType, list[Modifier]]:
        return self.definition.modifiers
    
    @property
    def duration(self) -> Duration:
        return self.definition.duration

    def is_over(self, state: State) -> bool:
        return self.definition.duration.is_over(state)
    
    def get_affected(self, state: State) -> list[HasModifiers]:
        return self.definition.targeting.get_affected(self.definition.source, state)
    
    def _register(self, obj: HasModifiers) -> None:
        if obj.try_register_cont_effect(self.key):
            self.state.currently_affected.add(obj)
    
    def _unregister(self, obj: HasModifiers) -> None:
        if obj.try_unregister_cont_effect(self.key):
            self.state.currently_affected.remove(obj)
    
    def attach(self, state: State) -> None:
        for target in self.get_affected(state):
            self._register(target)

    def detach(self, state: State) -> None:
        for target in self.get_affected(state):
            self._unregister(target)
    
    def on_event(self, event: GameEvent, state: State) -> None:
        new_affected_list = self.definition.targeting.on_event(event, self.definition.source, state)
        if new_affected_list is None: return

        new_affected = set(new_affected_list)
        added = new_affected - self.state.currently_affected
        removed = self.state.currently_affected - new_affected

        for target in added:
            self._register(target)
        for target in removed:
            self._unregister(target)
    

T = TypeVar("T")

class Modifier(Generic[T], ABC):

    @property
    @abstractmethod
    def layer(self) -> Layer: ...

    @property
    @abstractmethod
    def behavior(self) -> ModifierType: ...

    @abstractmethod
    def modify(self, original: T) -> T: ...


class AddModifier(Generic[T], Modifier[T], ABC):

    @property
    def layer(self):
        return Layer.ADD


@dataclass(frozen=True)
class SetModifier(Modifier[T]):
    
    value: T
    
    @property
    def layer(self):
        return Layer.SET
    
    def modify(self, original: T) -> T:
        return self.value


@dataclass(frozen=True)
class AddIntModifier(AddModifier[int]):
    
    value: int
    
    def modify(self, original: int) -> int:
        return original + self.value


@dataclass(frozen=True)
class MultiplyIntModifier(Modifier[int]):
    
    value: int
    
    @property
    def layer(self):
        return Layer.MULTIPLY
    
    def modify(self, original: int) -> int:
        return original * self.value


@dataclass(frozen=True)
class AddSetModifier(Generic[T], AddModifier[T]):
    
    value: frozenset[T]
    
    def modify(self, original: set[T]) -> set[T]:
        original.update(self.value)
        return original


@dataclass(frozen=True)
class RemoveSetModifier(Generic[T], AddModifier[set[T]]):
    
    value: frozenset[T]
    
    def modify(self, original: set[T]) -> set[T]:
        original.difference_update(self.value)
        return original
    

KT = TypeVar("KT", bound=KeyedObject)

@dataclass(frozen=True)
class AddCollectionModifier(Generic[KT], AddModifier[KeyedCollection[KT]]):

    value: list[KT]

    def modify(self, original: KeyedCollection[KT]) -> KeyedCollection[KT]:
        for o in self.value:
            if o.key in original:
                continue
            original.add(o)
        return original

@dataclass(frozen=True)
class AddManaCostModifier(AddModifier[ManaValue]):

    value: ManaValue

    def modify(self, value: ManaValue) -> ManaValue:
        raise NotImplementedError()

@dataclass 
class TimeStampedModifier:

    modifier: Modifier
    time_stamp: TimeStamp

    
class ContinuousEffectsManager:

    def __init__(
            self
        ) -> None:

        self._continuous_effects: dict[str, ContinuousEffect] = dict()

        self._time_stamp_heap: Heap[TimeStamp] = Heap()
        self._time_stamp_bucket: dict[TimeStamp, list[str]] = dict()

        self._moment_heap_map: dict[int, Heap[PlayerMoment]]  = dict()
        self._moment_buckets: dict[int, dict[PlayerMoment, list[str]]] = dict()



    def get(self, key: str) -> ContinuousEffect | None:
        return self._continuous_effects.get(key)
    

    def add(self, ce: ContinuousEffect) -> None:
        if ce.key in self._continuous_effects:
            raise ValueError(f"  Duplicate key '{ce.key}' in _continuous_effects.")
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
        while (
            self._time_stamp_heap
            and self._time_stamp_heap.peek() <= state.time_stamp
        ):
            
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
    
    @abstractmethod
    def get_modifiers(self, amount: int) -> list[Modifier]: ...


class PlusCounter(Counter): 

    def get_modifiers(self, amount):
        return {
            STAT_POWER: [AddIntModifier(amount)],
            STAT_TOUGHNESS: [AddIntModifier(amount)]
        }


STAT_TO_COUNTERS: dict[StatType, frozenset[CounterType]] = {
    STAT_POWER: {CounterType.PLUS_ONE},
    STAT_TOUGHNESS: {CounterType.PLUS_ONE}
}

TYPE_TO_COUNTER: dict[CounterType, Counter] = {
    CounterType.PLUS_ONE: PlusCounter()
}
        
class ModifierSource(ABC):

    @abstractmethod
    def get_modifiers(self, stat: StatType, state: State) -> dict[TimeStampedModifier]: ...


@dataclass
class ContinuouosEffectModifierSource(ModifierSource):

    active_cont_effects: set[str] = field(default_factory=set)

    def try_register_cont_effect(self, key: str) -> bool: 
        if key in self.active_cont_effects: 
            return False
        
        self.active_cont_effects.add(key)
        return True
    
    def try_unregister_cont_effect(self, key: str) -> bool:
        if key not in self.active_cont_effects:
            return False
        
        self.active_cont_effects.remove(key)
        return True
    
    def _get_cont_effect(self, key: str, state: State) -> ContinuousEffect | None:
        return state.get_cont_effect(key)
    
    def get_modifiers(self, stat, state):
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
                modifiers.append(TimeStampedModifier(mod, ce.definition.created_at))
        
        for key in invalid_keys:
            self.active_cont_effects.discard(key)
        
        return modifiers


@dataclass
class CounterModifierSource(ModifierSource):

    counters: dict[CounterType, int] | None = field(default_factory=dict)

    def get_modifiers(self, stat, state):
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

    @property
    @abstractmethod
    def attached_to(self) -> AttachedModifierSource | None:
        ...

    @attached_to.setter
    @abstractmethod
    def attached_to(self, value: AttachedModifierSource | None) -> None:
        ...

    @property
    @abstractmethod
    def last_attach_at(self) -> TimeStamp | None:
        ...
    
    @last_attach_at.setter
    @abstractmethod
    def last_attach_at(self, value: TimeStamp | None) -> None:
        ...
    
    @abstractmethod
    def modifiers_to_attach(self, state: State) -> dict[StatType, list[Modifier]] | None:
        ...

    def attach(self, attach_to: AttachedModifierSource, state: State) -> None:
        if self.attach_key in attach_to.attached:
            raise KeyError(f"  Duplicate key: '{self.attach_key}' in attach_to.")
        if self.attached_to is not None:
            self.detach()
        attach_to.attached[self.attach_key] = self
        self.attached_to = attach_to
        self.last_attach_at = state.time_stamp
        self._update_modifier_time(state)

    def detach(self) -> None:
        if self.attached_to is None:
            return
        
        if self.attach_key in self.attached_to.attached:
            self.attached_to.attached.pop(self.attach_key)
        
        self.attached_to = None
        self.last_attach_at = None

    def _update_modifier_time(self, state: State) -> None:
        if self.modifiers_to_attach is None:
            return
        
        for mods in self.modifiers_to_attach.values(state):
            for mod in mods:
                mod.created_at = self.last_attach_at


@dataclass
class AttachedModifierSource(ModifierSource):

    attached: dict[str, Attachable] | None = field(default_factory=dict)

    def get_modifiers(self, stat, state):
        modifiers: list[Modifier] = [] 
        if not self.attached:
            return modifiers
        
        for v in self.attached.values():
            mas = v.modifiers_to_attach.get(stat)
            if mas is None:
                continue
            
            for mod in mas:
                modifiers.append(TimeStampedModifier(mod, v.last_attach_at))
        
        return modifiers
    
       

                