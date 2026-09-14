"""Validate selected targets and prepare resolutions against live game state."""

from __future__ import annotations

from typing import TYPE_CHECKING
from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Iterable

if TYPE_CHECKING:
    from ...game_state import State
    from ..data_structs.game_action import ScheduledResolution

from ...enums import *


@dataclass(frozen=True)
class ValidationResult:
    """!
    @brief Result of validating a scheduled resolution.

    @var success
        Whether validation succeeded.
    @var message
        Human-readable failure reason, or `None` on success.
    """

    success: bool = True
    message: str | None = None


class Validator(ABC):
    """!
    @brief Base interface for live-state resolution validation.
    """

    @abstractmethod
    def validate(
        self,
        state: State,
        resolution: ScheduledResolution,
    ) -> ValidationResult:
        """!
        @brief Validate a resolution against the current game state.

        @param state Current game state.
        @param resolution Resolution being checked.
        @return Validation result.
        """
        ...


class CompositeValidator(Validator):
    """!
    @brief Run several validators in sequence and return the first failure.
    """

    def __init__(self, validators: Iterable[Validator]):
        """!
        @brief Create a validator from an ordered validator sequence.

        @param validators Validators to evaluate in order.
        """
        self.validators = list(validators)

    def validate(self, state, resolution):
        """!
        @brief Validate using each configured validator until one fails.
        """
        for validator in self.validators:
            result = validator.validate(state, resolution)

            if not result.success:
                return result

        return ValidationResult()


class ZoneValidator(Validator):
    """!
    @brief Validate that an ability source is still in a usable zone.
    """

    def validate(self, state, resolution):
        """!
        @brief Check source-zone legality for ability cost resolution.
        """
        source = resolution.context.source
        ability = resolution.context.ability

        if (
            ability is not None
            and resolution.context.is_cost
            and not ability.is_usable_in_zone(source.get_zone())
        ):
            return ValidationResult(
                False,
                f"Card {source} is not in a valid zone.",
            )

        return ValidationResult()


class TimingValidator(Validator):
    """!
    @brief Recheck ability-level legality immediately before paying costs.
    """

    def validate(self, state, resolution):
        """!
        @brief Evaluate the ability's live validation hook for cost resolution.
        """
        context = resolution.context

        if context.is_cost and context.ability is not None:
            error = context.ability.validation_error(
                context.source,
                context.controller,
                state,
            )

            if error:
                return ValidationResult(False, error)

        return ValidationResult()


class TargetValidator(Validator):
    """!
    @brief Validate selected targets against current zones and target rules.
    """

    def validate(
        self,
        state,
        resolution,
    ) -> ValidationResult:
        """!
        @brief Check whether all currently required selected targets remain legal.

        Bindings already filtered during resolution preparation are accepted
        without repeating full target validation.

        @param state Current game state.
        @param resolution Resolution whose target binding should be checked.
        @return Validation result.
        """
        if resolution.context.targets is None:
            return ValidationResult()

        # `prepare_resolution()` has already removed targets that became
        # illegal during resolution.
        if getattr(
            resolution.context.targets,
            "resolution_filtered",
            False,
        ):
            return ValidationResult()

        if resolution.context.effects is None:
            return ValidationResult(
                not bool(resolution.context.targets),
                "Missing target slot definitions.",
            )

        used_slots = resolution.context.effects.get_used_slots()

        revisions = getattr(
            resolution.context.targets,
            "zone_revisions",
            {},
        )

        # A card that changed zones is a new game object for targeting
        # purposes even when the same Python object identity is reused.
        for inner in resolution.context.targets.values():
            for group in inner.values():
                for target in group:
                    if (
                        id(target) in revisions
                        and target.zone_revision != revisions[id(target)]
                    ):
                        return ValidationResult(
                            False,
                            "A target changed zones since it was selected.",
                        )

        if not resolution.context.targets.are_targets_valid(
            resolution.context.source,
            resolution.context.controller,
            state,
            used_slots,
        ):
            return ValidationResult(
                False,
                "Targets were no longer valid.",
            )

        return ValidationResult()


def prepare_resolution(state, resolution):
    """!
    @brief Remove illegal targets immediately before effect resolution.

    Implements the resolution-time target rule: targets that became illegal
    are removed individually, while the whole resolution fails only when it
    originally had targets and none of them remain legal.

    Cost resolutions and generators other than `AbilityExecutionPlan` are left
    unchanged.

    @param state Current game state.
    @param resolution Scheduled resolution to prepare.
    @return Tuple `(prepared_resolution, validation_result)`.
    """
    from dataclasses import replace
    from ..data_structs.game_action import AbilityExecutionPlan, ScheduledResolution
    from ...target.target_resolver import TargetBinding, TargetOption

    if (
        resolution.context.is_cost
        or not isinstance(
            resolution.generator,
            AbilityExecutionPlan,
        )
    ):
        return resolution, ValidationResult()

    original = resolution.generator.binding

    revisions = getattr(
        original,
        "zone_revisions",
        {},
    )

    selected = 0
    valid_count = 0
    binding = TargetBinding()

    # Rebuild the binding slot by slot, keeping only targets that are still
    # legal in the live state.
    for wrapper in resolution.generator.effects.get_used_slots():
        option = original.get(
            wrapper.slot.key,
            {},
        ).get(
            wrapper.runtime_key,
            {},
        )

        selected += len(option)
        valid = {}

        for target, count in option.items():
            # Zone revision distinguishes the originally selected incarnation
            # from a card object that has since left and re-entered a zone.
            if (
                id(target) in revisions
                and target.zone_revision != revisions[id(target)]
            ):
                continue

            if wrapper.is_target_valid(
                target,
                resolution.context.source,
                resolution.context.controller,
                state,
            ):
                valid[target] = count

        valid_count += len(valid)

        binding.setdefault(
            wrapper.slot.key,
            {},
        )[wrapper.runtime_key] = TargetOption(valid)

    # A targeted spell/ability fails only if every originally selected target
    # has become illegal.
    if selected and not valid_count:
        return (
            resolution,
            ValidationResult(
                False,
                "All targets are no longer legal.",
            ),
        )

    filtered = binding.to_immutable()

    # Prevent later TargetValidator passes from demanding that removed targets
    # still satisfy the original complete binding constraints.
    filtered.resolution_filtered = True

    generator = replace(
        resolution.generator,
        binding=filtered,
    )

    return (
        ScheduledResolution(
            generator,
            resolution.context,
        ),
        ValidationResult(),
    )