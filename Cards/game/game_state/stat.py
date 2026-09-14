"""Characteristic evaluation through ordered modifier pipelines."""

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
    """!
    @brief Base representation of one typed characteristic.

    @var base_value
        Printed or otherwise intrinsic value before runtime modifiers.
    @var stat_type
        Identifier describing the represented characteristic.
    """

    base_value: T
    stat_type: StatType[T]

    @abstractmethod
    def value(self, state: State, modifiers: tuple[Modifier[MT], ...] | None) -> T:
        """!
        @brief Evaluate this characteristic using an optional modifier pipeline.

        @param state Current game state.
        @param modifiers Ordered modifiers affecting this characteristic.
        @return Evaluated characteristic value.
        """
        ...


class ModifiableStatBase[T, MT](StatBase[T, MT], ABC):
    """!
    @brief Base class for characteristics evaluated through runtime modifiers.
    """

    def _get_modifiers_start_idx(self, modifiers: tuple[Modifier[MT], ...]) -> int:
        """!
        @brief Find the last SET modifier in a pipeline.

        @param modifiers Ordered modifier pipeline.
        @return Index of the last SET modifier, or zero when none exists.
        """
        for i in range(len(modifiers) - 1, -1, -1):
            if modifiers[i].behavior == ModifierType.SET:
                return i

        return 0

    def _resolve_modifiers(
        self, value: MT, state: State, modifiers: tuple[Modifier[MT], ...]
    ) -> MT:
        """!
        @brief Apply an already ordered modifier pipeline to a mutable value.

        Layer ordering is resolved before this method is called, so modifiers
        are applied strictly in pipeline order.

        @param value Initial mutable characteristic value.
        @param state Current game state.
        @param modifiers Ordered modifiers to apply.
        @return Fully modified value.
        """
        start_idx = 0  # A later SET may be in an earlier layer than another operation.

        for i in range(start_idx, len(modifiers)):
            value = modifiers[i].modify(value)

        return value


class Stat[T](StatBase[T, T]):
    """!
    @brief Non-modifiable characteristic that always exposes its base value.
    """

    @override
    def value(self, state, modifiers=None):
        """!
        @brief Return the intrinsic value without applying modifiers.
        """
        return self.base_value


class ModifiablePrimitiveStat[T](ModifiableStatBase[T, T]):
    """!
    @brief Modifiable characteristic whose value can be transformed directly.
    """

    @override
    def value(self, state, modifiers):
        """!
        @brief Evaluate a primitive characteristic through its modifier pipeline.
        """
        if not modifiers:
            return self.base_value

        return self._resolve_modifiers(self.base_value, state, modifiers)


class ModifiableReferenceStat[MCT: ToImmutableConvertible, ICT: ToMutableConvertible[MCT]](
    ModifiableStatBase[ICT, MCT]
):
    """!
    @brief Modifiable immutable characteristic evaluated through a mutable copy.

    Reference-like characteristic values are converted to mutable form before
    applying modifiers and converted back to their immutable representation
    afterwards.
    """

    @override
    def value(self, state, modifiers):
        """!
        @brief Evaluate modifiers without mutating the stored base value.
        """
        if not modifiers:
            return self.base_value

        v = self.base_value.to_mutable()
        v = self._resolve_modifiers(v, state, modifiers)

        return cast(ICT, v.to_immutable())


class HasStats(ABC):
    """!
    @brief Interface for runtime objects exposing typed characteristics.
    """

    @property
    @abstractmethod
    def stats(self) -> Mapping[StatType, Stat]:
        """!
        @brief Return the object's characteristic definitions.
        """
        ...

    @abstractmethod
    def get_stat[T](self, stat_t: StatType[T], state: State) -> T | None:
        """!
        @brief Evaluate one characteristic in the supplied state.

        @param stat_t Characteristic identifier.
        @param state Current game state.
        @return Evaluated value, or `None` if the characteristic is absent.
        """
        ...


@dataclass(frozen=True)
class HasModifiers(ABC):
    """!
    @brief Mixin for objects receiving modifiers from runtime modifier sources.
    """

    @property
    @abstractmethod
    def modifier_sources(self) -> tuple[ModifierSource, ...] | None:
        """!
        @brief Return all sources contributing modifiers to this object.
        """
        ...

    def _get_modifier_pipeline[T](
        self, stat_t: StatType[T], state: State
    ) -> tuple[Modifier[T]]:
        """!
        @brief Build the ordered modifier pipeline for one characteristic.

        Modifiers are collected from every source, grouped by CR 613 layer,
        filtered by the temporary layer ceiling when continuous effects are
        being evaluated, and then ordered by dependencies and timestamps within
        each layer.

        @param stat_t Characteristic being evaluated.
        @param state Current game state.
        @return Ordered tuple of raw modifiers.
        """
        modifiers: list[TimeStampedModifier] = []

        for source in self.modifier_sources:
            modifiers.extend(source.get_modifiers(stat_t, state))

        from .layers import modifier_layer, dependency_order

        ceiling = getattr(state, "_layer_ceiling", None)
        groups = {}

        for modifier in modifiers:
            layer = modifier_layer(stat_t, modifier.modifier)

            # Continuous-effect refresh may temporarily expose only layers up to
            # the currently evaluated one.
            if ceiling is None or layer.value <= ceiling.value:
                groups.setdefault(layer.value, []).append(modifier)

        ordered = []

        for layer in sorted(groups):
            ordered.extend(
                dependency_order(
                    groups[layer],
                    key=lambda m: m.effect_key or str(id(m)),
                    dependencies=lambda m: m.depends_on,
                    timestamp=lambda m: (m.time_stamp, m.order),
                )
            )

        return tuple(m.modifier for m in ordered)


class HasModifiableStats(HasStats, HasModifiers, ABC):
    """!
    @brief Base for runtime objects whose characteristics can be modified.
    """

    def get_base_value[T](self, stat_t: StatType[T]) -> T | None:
        """!
        @brief Return the unmodified value of a characteristic.

        @param stat_t Characteristic identifier.
        @return Base value, or `None` if the characteristic is absent.
        """
        stat = self.stats.get(stat_t)

        if not stat:
            return None

        return stat.base_value

    @override
    def get_stat[T](self, stat_t: StatType[T], state: State) -> T | None:
        """!
        @brief Evaluate one characteristic using the current modifier pipeline.

        Power/toughness switching is handled specially: an odd number of switch
        modifiers causes the characteristic to read the opposite P/T value,
        evaluated without recursively applying switch modifiers.

        @param stat_t Characteristic identifier.
        @param state Current game state.
        @return Evaluated value, or `None` if the characteristic is absent.
        """
        stat = self.stats.get(stat_t)

        if stat is None:
            return None

        if not isinstance(stat, ModifiableStatBase):
            return stat.base_value

        pipeline = self._get_modifier_pipeline(stat_t, state)

        if not pipeline:
            return stat.base_value

        from ..enums import ModifierType
        from ..stat_type import STAT_POWER, STAT_TOUGHNESS

        if stat_t in (STAT_POWER, STAT_TOUGHNESS):
            # Multiple switch effects cancel pairwise. With an odd number, read
            # the opposite characteristic at its pre-switch value.
            if sum(mod.behavior == ModifierType.SWITCH for mod in pipeline) % 2:
                other = STAT_TOUGHNESS if stat_t == STAT_POWER else STAT_POWER
                other_stat = self.stats.get(other)

                if other_stat is None:
                    return None

                other_pipeline = tuple(
                    mod
                    for mod in self._get_modifier_pipeline(other, state)
                    if mod.behavior != ModifierType.SWITCH
                )

                return other_stat.value(state, other_pipeline)

        return stat.value(state, pipeline)