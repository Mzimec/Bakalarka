"""Target slots, bound choices and validation against live state."""

from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass
from immutabledict import immutabledict
from abc import ABC
from collections.abc import Mapping, Iterable, Set, Iterator

if TYPE_CHECKING:
    from ..game_state import Card, State, Player
    from .target_spec import TargetSpec
    from .target_selector import TargetSelector
from helper.runtime_object import RuntimeObject

__all__ = ["TargetBinding", "TargetConstraint", "TargetSlot", "TargetResolver"]

TargetOption = immutabledict[RuntimeObject, int]


class TargetBindingBase(Mapping[str, Mapping[str, TargetOption]], ABC):
    """!
    @brief Shared read-only interface for runtime target bindings.
    """

    def get_targets_in_slot(self, runtime_key: str, key: str) -> frozenset[RuntimeObject] | None:
        """!
        @brief Return the targets bound to one runtime instance of a target slot.

        @param runtime_key Runtime occurrence key.
        @param key Static target-slot key.
        @return Bound targets, or `None` if the slot occurrence is absent.
        """
        if key not in self:
            return None
        inner = self[key]
        if runtime_key not in inner:
            return None
        return frozenset(inner[runtime_key].keys())

    def are_targets_valid(
        self,
        source: Card,
        controller: Player,
        state: State,
        slots: Iterable[RepetitionTargetSlotWrapper],
    ) -> bool:
        """!
        @brief Validate this complete binding against the current game state.

        The binding must contain exactly the expected runtime slot occurrences.
        Each target group must satisfy its selector and every individual target
        is revalidated against live state and distinctness reservations.

        @param source Ability source.
        @param controller Controller choosing the targets.
        @param state Current game state.
        @param slots Runtime target-slot occurrences expected by the plan.
        @return Whether the complete binding is currently legal.
        """
        slots = tuple(slots)
        expected = {(slot.slot.key, slot.runtime_key) for slot in slots}
        actual = {(key, runtime_key) for key, inner in self.items() for runtime_key in inner}

        if actual != expected:
            return False

        for slot in slots:
            option = self[slot.slot.key][slot.runtime_key]
            resolver = slot.slot.target_resolver

            if not resolver.target_selector.is_valid_option(option):
                return False

            # Distinctness is evaluated against the complete binding, so every
            # target is checked with objects reserved by related target slots.
            reserved = slot.get_reserved_objs(self)

            for target in option:
                if not resolver.is_valid_target(target, source, controller, state, reserved):
                    return False

        return True


class ImmutableTargetBinding(
    immutabledict[str, immutabledict[str, TargetOption]], TargetBindingBase
):
    """!
    @brief Immutable target binding stored on a generated action.

    Zone revisions are captured when the binding is created so resolution can
    detect targets that left and re-entered before the spell or ability resolves.
    """

    def __init__(self, *args, **kwargs):
        self.zone_revisions = {
            id(target): target.zone_revision
            for inner in self.values()
            for group in inner.values()
            for target in group
            if hasattr(target, "zone_revision")
        }
        self.resolution_filtered = False

    def to_mutable(self) -> TargetBinding:
        """!
        @brief Return a mutable representation of this value.
        """
        return TargetBinding({k: dict(v) for k, v in self.items()})


class TargetBinding(dict[str, dict[str, TargetOption]], TargetBindingBase):
    """!
    @brief Mutable target binding used while generating and backtracking choices.
    """

    def to_immutable(self) -> ImmutableTargetBinding:
        """!
        @brief Freeze this binding for storage on a generated action.
        """
        return ImmutableTargetBinding({k: immutabledict(v) for k, v in self.items()})


class TargetConstraint:
    """!
    @brief Placeholder base for future target validation constraints.
    """

    pass


@dataclass(frozen=True)
class TargetSlot:
    """!
    @brief Named target requirement used by effects in an ability.

    @var key
        Static slot identifier.
    @var target_resolver
        Candidate discovery and selection rules for this slot.
    @var distinct_from
        Static slot keys whose targets must be reserved against this slot.
    """

    key: str
    target_resolver: TargetResolver
    distinct_from: frozenset[str]


@dataclass(frozen=True)
class RepetitionTargetSlotWrapper:
    """!
    @brief One runtime occurrence of a potentially repeated target slot.

    `runtime_key` distinguishes separate occurrences generated from the same
    static `TargetSlot`.
    """

    runtime_key: str
    slot: TargetSlot

    def get_reserved_objs(self, binding: TargetBindingBase) -> frozenset[RuntimeObject]:
        """!
        @brief Collect objects already used by slots declared distinct from this one.

        All runtime instances of every referenced static slot participate in
        the reservation.

        @param binding Current partial or complete target binding.
        @return Objects unavailable to this slot because of distinctness rules.
        """
        reserved: set[RuntimeObject] = set()

        for distinct in self.slot.distinct_from:
            if distinct in binding:
                for k in binding[distinct].keys():
                    targets: frozenset[RuntimeObject] | None = binding.get_targets_in_slot(
                        k, distinct
                    )
                    if targets:
                        reserved.update(targets)

        return frozenset(reserved)

    def count_options(
        self, source: Card, controller: Player, state: State, binding: TargetBindingBase
    ) -> int:
        """!
        @brief Count legal options for this runtime slot under the current binding.
        """
        reserved = self.get_reserved_objs(binding)
        return self.slot.target_resolver.count_options(source, controller, state, reserved)

    def is_target_valid(
        self, target: RuntimeObject, source: Card, controller: Player, state: State
    ) -> bool:
        """!
        @brief Check one candidate against this slot without binding reservations.
        """
        return self.slot.target_resolver.is_valid_target(target, source, controller, state)


@dataclass(frozen=True)
class TargetResolver:
    """!
    @brief Combines candidate discovery with target selection strategy.

    `TargetSpec` determines which individual runtime objects are legal while
    `TargetSelector` determines which groups and multiplicities form a legal
    target option.
    """

    target_spec: TargetSpec
    target_selector: TargetSelector

    def is_valid_target(self, target, source, controller, state, reserved=None):
        """!
        @brief Check whether one candidate is currently legal.

        Battlefield shroud and opposing hexproof are enforced before delegating
        to the underlying target specification.

        @param target Candidate runtime object.
        @param source Ability source.
        @param controller Controller choosing the target.
        @param state Current game state.
        @param reserved Objects unavailable because of cross-slot constraints.
        @return Whether the candidate may be targeted.
        """
        from ..enums import ZoneType

        if hasattr(target, "has_keyword") and target.get_zone() == ZoneType.BATTLEFIELD:
            if target.has_keyword(state, "shroud"):
                return False
            if (
                target.has_keyword(state, "hexproof")
                and target.get_controller(state) is not controller
            ):
                return False

        return self.target_spec.is_valid_target(target, source, controller, state, reserved)

    def generate_target_options(
        self,
        source: Card,
        controller: Player,
        state: State,
        reserved: Set[RuntimeObject] | None = None,
    ) -> Iterator[TargetOption]:
        """!
        @brief Generate legal target groups for this resolver.

        Candidate objects are first filtered through live target legality, then
        passed to the selector which builds legal groups and repetition counts.

        @param source Ability source.
        @param controller Controller choosing targets.
        @param state Current game state.
        @param reserved Objects excluded by other target slots.
        @return Iterator of legal target options.
        """
        candidates = (
            target
            for target in self.target_spec.generate_candidates(source, controller, state, reserved)
            if self.is_valid_target(target, source, controller, state, reserved)
        )
        yield from self.target_selector.generate_target_options(candidates)

    def count_options(
        self,
        source: Card,
        controller: Player,
        state: State,
        reserved: Set[RuntimeObject] | None = None,
    ) -> int:
        """!
        @brief Count legal target groups without materializing them.

        @param source Ability source.
        @param controller Controller choosing targets.
        @param state Current game state.
        @param reserved Objects excluded by other target slots.
        @return Number of legal target options.
        """
        candidates = (
            target
            for target in self.target_spec.generate_candidates(source, controller, state, reserved)
            if self.is_valid_target(target, source, controller, state, reserved)
        )
        return self.target_selector.count_options(candidates)