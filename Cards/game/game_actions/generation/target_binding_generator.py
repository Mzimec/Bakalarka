"""Generate bindings from supplied targets or legal candidate sets."""

from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator, Iterable
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from .ability_action_gen_pipeline import ActionGenerationContextBase
    from ...game_state import State
    from helper.runtime_object import RuntimeObject

from ...target.target_resolver import (
    ImmutableTargetBinding,
    TargetBinding,
    RepetitionTargetSlotWrapper,
    TargetOption,
)


class TargetBindingGenerator(ABC):
    """!
    @brief Interface for producing target bindings for used target slots.
    """

    @abstractmethod
    def generate(
        self,
        ctx: ActionGenerationContextBase,
        used_slots: Iterable[RepetitionTargetSlotWrapper],
        state: State,
    ) -> Iterator[ImmutableTargetBinding]:
        """!
        @brief Yield legal immutable target bindings.
        """
        ...


class ProvidedTargetBindingGenerator(TargetBindingGenerator):
    """!
    @brief Bind exactly the targets supplied by the player, without a search.
    """

    def __init__(self, targets=()):
        """!
        @brief Store the explicit target selection to validate.

        @param targets Sequence of targets corresponding to the used
               target slots.
        """
        self.targets = tuple(targets)

    def generate(self, ctx, used_slots, state):
        """!
        @brief Validate supplied targets and yield their binding.

        Used when the caller has already selected targets and only needs
        to verify that the selection is legal for the current ability
        and state.

        @param ctx Shared action-generation context.
        @param used_slots Target slots required by the selected effects.
        @param state Current game state.
        @return Iterator containing the validated target binding.
        @throws ValueError If the number of supplied targets is wrong or
                any supplied target violates slot or cross-slot constraints.
        """
        # Keep a deterministic order so supplied targets map consistently
        # to their corresponding runtime slots.
        slots = sorted(
            used_slots,
            key=lambda slot: (slot.slot.key, slot.runtime_key),
        )

        if len(slots) != len(self.targets):
            if not slots:
                raise ValueError("This ability is used without a target.")

            raise ValueError(
                "Supply a target for each slot: "
                + ", ".join(slot.slot.key for slot in slots)
            )

        binding = TargetBinding()

        for slot, target in zip(slots, self.targets):
            from collections import Counter

            # A slot may accept either one object or a group of repeated
            # targets. TargetOption stores the resulting object counts.
            group = target if isinstance(target, (tuple, list)) else (target,)
            option = TargetOption(dict(Counter(group)))

            resolver = slot.slot.target_resolver

            if not resolver.target_selector.is_valid_option(option):
                raise ValueError(
                    f"Invalid target count for slot '{slot.slot.key}'."
                )

            # Validate each supplied object while respecting targets that
            # have already been reserved by earlier slots.
            if any(
                not resolver.is_valid_target(
                    item,
                    ctx.ability.source,
                    ctx.ability.controller,
                    state,
                    slot.get_reserved_objs(binding),
                )
                for item in group
            ):
                raise ValueError(
                    f"Invalid target for slot '{slot.slot.key}'."
                )

            binding.setdefault(slot.slot.key, {})[slot.runtime_key] = option

        # Validate constraints involving multiple slots only after the
        # complete binding has been assembled.
        if not binding.are_targets_valid(
            ctx.ability.source,
            ctx.ability.controller,
            state,
            slots,
        ):
            raise ValueError("Targets violate this ability's constraints.")

        yield binding.to_immutable()


class FullTargetBindingGenerator(TargetBindingGenerator):
    """!
    @brief Enumerate every legal target binding for the used target slots.
    """

    @override
    def generate(
        self,
        ctx,
        used_slots,
        state,
    ) -> Iterator[ImmutableTargetBinding]:
        """!
        @brief Yield all legal target combinations.

        Target slots are assigned recursively. At each step, the slot
        with the fewest currently available options is selected first
        to reduce the branching factor.

        @param ctx Shared action-generation context.
        @param used_slots Target slots required by the selected effects.
        @param state Current game state.
        @return Iterator of legal immutable target bindings.
        """

        def _recursion_step(
            binding: TargetBinding,
        ) -> Iterator[ImmutableTargetBinding]:
            # Once every slot has been assigned, validate constraints that
            # depend on the complete target combination.
            if not remaining_slots:
                if binding.are_targets_valid(
                    ctx.ability.source,
                    ctx.ability.controller,
                    state,
                    used_slots,
                ):
                    yield binding.to_immutable()
                return

            # Prefer the most constrained slot first to prune invalid
            # branches as early as possible.
            slot: RepetitionTargetSlotWrapper = min(
                remaining_slots,
                key=lambda s: s.count_options(
                    ctx.ability.source,
                    ctx.ability.controller,
                    state,
                    binding,
                ),
            )

            reserved: frozenset[RuntimeObject] = slot.get_reserved_objs(binding)

            slot_bindings = binding.setdefault(slot.slot.key, {})

            remaining_slots.remove(slot)

            for option in slot.slot.target_resolver.generate_target_options(
                ctx.ability.source,
                ctx.ability.controller,
                state,
                reserved,
            ):
                slot_bindings[slot.runtime_key] = option

                # Backtrack after each recursive branch so the same mutable
                # binding object can be reused without copying.
                try:
                    yield from _recursion_step(binding)
                finally:
                    del slot_bindings[slot.runtime_key]

            if not slot_bindings:
                del binding[slot.slot.key]

            remaining_slots.add(slot)

        used_slots = tuple(used_slots)
        remaining_slots: set[RepetitionTargetSlotWrapper] = set(used_slots)

        yield from _recursion_step(TargetBinding())
