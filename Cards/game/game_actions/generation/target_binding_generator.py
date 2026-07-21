from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator, Iterable
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from .ability_action_gen_pipeline import ActionGenerationContextBase
    from ...game_state import State
    from ....helper.runtime_object import RuntimeObject

from ...target.target_resolver import ImmutableTargetBinding, TargetBinding, RepetitionTargetSlotWrapper, TargetOption


class TargetBindingGenerator(ABC):

    @abstractmethod
    def generate(
        self, 
        ctx: ActionGenerationContextBase, 
        used_slots: Iterable[RepetitionTargetSlotWrapper], 
        state: State
    ) -> Iterator[ImmutableTargetBinding]: ...


class FullTargetBindingGenerator(TargetBindingGenerator):

    @override
    def generate(self, ctx, used_slots, state) -> Iterator[ImmutableTargetBinding]:
        
        def _recursion_step(binding: TargetBinding) -> Iterator[ImmutableTargetBinding]:

            if not remaining_slots:
                yield binding.to_immutable()
                return
            
            slot: RepetitionTargetSlotWrapper = min(
                remaining_slots,
                key=lambda s: s.count_options(
                    ctx.ability.source, 
                    ctx.ability.controller,
                    state, 
                    binding
                )
            )

            reserved: frozenset[RuntimeObject] = slot.get_reserved_objs(binding)
            
            slot_bindings = binding.setdefault(slot.slot.key, {})

            remaining_slots.remove(slot)

            for option in slot.slot.target_resolver.generate_target_options(
                ctx.ability.source, 
                ctx.ability.controller,
                state, 
                reserved
            ):
                
                slot_bindings[slot.runtime_key] = option

                try:
                    yield from _recursion_step(binding)
                finally:
                    del slot_bindings[slot.runtime_key]

            if not slot_bindings:
                del binding[slot.slot.key]

            remaining_slots.add(slot)
    
        remaining_slots: set[RepetitionTargetSlotWrapper] = set(used_slots)
        yield from _recursion_step(TargetBinding())

