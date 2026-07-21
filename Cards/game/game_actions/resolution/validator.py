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
    @brief Describes why an action intent cannot be executed.
    """
    success: bool = True
    message: str | None = None

class Validator(ABC):

    @abstractmethod
    def validate(
        self,
        state: State,
        resolution: ScheduledResolution
    ) -> ValidationResult:
        ...


class CompositeValidator(Validator):

    def __init__(self, validators: Iterable[Validator]):
        self.validators = list(validators)

    def validate(self, state, resolution):
        for v in self.validators:
            res = v.validate(state, resolution)
            if not res.success:
                return res
        return ValidationResult()


class ZoneValidator(Validator):

    def validate(self, state, resolution):
        source = resolution.context.source
        if not resolution.context.ability.is_usable_in_zone(source.get_zone()):
            return ValidationResult(False, f"Card {source} is not in a valid zone.")
        return ValidationResult()


class TimingValidator(Validator):

    def validate(self, state, resolution):
        if resolution.context.uses_stack and not resolution.context.ability.is_instant: # TODO chybi is_inastant
            if not state.is_main_phase or state.stack: # TODO chybi is_main_phase
                return ValidationResult(False, "Sorcery speed ability cannot be used now.")
        return ValidationResult()
    

class TargetValidator(Validator):

    def validate(self, state, resolution) -> ValidationResult:
        if not resolution.context.targets:
            return ValidationResult()
        
        used_slots = resolution.context.effects.get_used_slots()
        if not resolution.context.targets.are_targets_valid(
            resolution.context.source, 
            resolution.context.controller, 
            state, used_slots
        ):
            return ValidationResult(False, "Targets were no longer valid.")

        return ValidationResult()
    
