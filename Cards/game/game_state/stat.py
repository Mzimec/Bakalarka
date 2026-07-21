from __future__ import annotations
from typing import TYPE_CHECKING, Any, override, cast
from dataclasses import dataclass
from abc import ABC, abstractmethod
from collections.abc import Iterable, Set, Mapping
from immutabledict import immutabledict
import copy

if TYPE_CHECKING:
    from .modifier import Modifier, ModifierSource, TimeStampedModifier
    from .state import State

from ..stat_type import *
from helper.mutability_objs import ToImmutableConvertible, ToMutableConvertible
from ..enums import * 




@dataclass(frozen=True)
class StatBase[T, MT](ABC):

    base_value: T
    stat_type: StatType[T]

    @abstractmethod
    def value(self, state: State, modifiers: tuple[Modifier[MT], ...] | None) -> T:
        ...


class ModifiableStatBase[T, MT](StatBase[T, MT], ABC):
    
    def _get_modifiers_start_idx(self, modifiers: tuple[Modifier[MT], ...]) -> int:
        for i in range(len(modifiers) - 1, -1, -1):
            if modifiers[i].behavior == ModifierType.SET:
                return i
        
        return 0
    
    def _resolve_modifiers(self, value: MT, state: State, modifiers: tuple[Modifier[MT], ...]) -> MT:
        start_idx = self._get_modifiers_start_idx(modifiers)
        for i in range(start_idx, len(modifiers)):
            value = modifiers[i].modify(value)
        return value
    


class Stat[T](StatBase[T, T]):

    @override
    def value(self, state, modifiers = None):
        return self.base_value


class ModifiablePrimitiveStat[T](ModifiableStatBase[T, T]):

    @override 
    def value(self, state, modifiers):
        if not modifiers:
            return self.base_value
        
        return self._resolve_modifiers(self.base_value, state, modifiers)


class ModifiableReferenceStat[
    MCT: ToImmutableConvertible, 
    ICT: ToMutableConvertible[MCT]
](ModifiableStatBase[ICT, MCT]):
    
    @override
    def value(self, state, modifiers):
        if not modifiers:
            return self.base_value
        
        v = self.base_value.to_mutable()
        v = self._resolve_modifiers(v, state, modifiers)

        return cast(ICT, v.to_immutable())


class HasStats(ABC):

    @property
    @abstractmethod
    def stats(self) -> Mapping[StatType, Stat]: 
        ...
    
    @abstractmethod
    def get_stat[T](self, stat_t: StatType[T], state: State) -> T | None: 
        ...

  
@dataclass(frozen=True)
class HasModifiers(ABC):

    @property
    @abstractmethod
    def modifier_sources(self) -> tuple[ModifierSource, ...] | None: 
        ...

    def _get_modifier_pipeline[T](self, stat_t: StatType[T], state: State) -> tuple[Modifier[T]]:
        modifiers: list[TimeStampedModifier] = []

        for source in self.modifier_sources:
            modifiers.extend(source.get_modifiers(stat_t, state))
        
        modifiers = sorted(modifiers, key=lambda m: (m.modifier.layer.value, m.time_stamp))
        return tuple(m.modifier for m in modifiers)
    

class HasModifiableStats(HasStats, HasModifiers, ABC):

    def get_base_value[T](self, stat_t: StatType[T]) -> T | None:
        stat = self.stats.get(stat_t)
        if not stat:
            return None
        return stat.base_value

    @override
    def get_stat[T](self, stat_t: StatType[T], state: State) -> T | None:
        stat = self.stats.get(stat_t)

        if stat is None:
            return None
        
        if not isinstance(stat, ModifiableStatBase):
            return stat.base_value
        
        pipeline = self._get_modifier_pipeline(stat_t, state)

        if not pipeline:
            return stat.base_value
        
        return stat.value(state, pipeline)