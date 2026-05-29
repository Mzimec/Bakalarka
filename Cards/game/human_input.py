from __future__ import annotations
from typing import TYPE_CHECKING, Any
from enum import Enum, auto
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from .game_state.player import DecisionMaker
    from .game_actions import GameAction
    from .game_state import State, Player, Card
    from .game_loop.game_loop import TurnPhase
    from .abilities import AbilityDefinition
    from .target import TargetBinding

class HumanDecionMaker(DecisionMaker):
    pass


class BuildingStepType(Enum):
    COMMAND = auto()
    SOURCE = auto()
    MODE = auto()
    COST = auto()
    TARGET = auto()

class SourceType(Enum):
    CARD = auto()
    PLAYER = auto()
    ABILITY = auto()
    NONE = auto()

@dataclass(frozen=True)
class BuildValidationResult:
    success: bool
    message: str

    @staticmethod
    def success() -> BuildValidationResult:
        return BuildValidationResult(success=True, message="")
    
    @staticmethod
    def fail(message: str) -> BuildValidationResult:
        return BuildValidationResult(success=False, message=message)
    

@dataclass(frozen=True)
class ActionDefinition:
    name: str

    requires_mode: bool
    requires_targets: bool
    requires_cost_payment: bool

    ability_def: AbilityDefinition


@dataclass
class ActionBuildContext:

    player: Player

    command: str

    action_def: ActionDefinition | None = None

    source: Any | None = None
    source_type: SourceType

    mode: int | None = None

    cost_binding: TargetBinding | None = None
    target_biding: TargetBinding | None = None


class BuildStep(ABC):

    context: ActionBuildContext

    def build(self) -> ActionBuildContext:
        while True:
            obj, res = self._find()
            if not res.success:
                print(res.message)
                continue

            context = self._set(context, obj)

            validation_result = self._validate(context)
            if not validation_result.success:
                print(validation_result.message)
                continue
            
            while True:
                apply_result = self._apply()
                if not apply_result.success:
                    if apply_result.message == "":
                        break
                    else:
                        print(apply_result.message)
                        continue
                else:
                    return context
         
    def _apply(self, promt: str) -> BuildValidationResult:
        answer = input(promt)
        if answer != "y" or answer != "n":
            return BuildValidationResult.fail(f"Unrecognized answer to apply: {answer}, Correct is y or n")
        elif answer == "n":
            return BuildValidationResult.fail("")
        else:
            return BuildValidationResult.success()

    @abstractmethod
    def _find(self, state: State) -> tuple[Any, BuildValidationResult]:
        pass

    @abstractmethod
    def _set(self, obj: Any) -> None:
        pass


    def _validate(self) -> BuildValidationResult:
        return BuildValidationResult.success()


POSSIBLE_COMMANDS: set[str] = {

}

VALID_COMMANDS_IN_PHASE: dict[TurnPhase, set[str]]


class CommandBuildStep(BuildStep): 

    def _find(self, state: State) -> tuple[Any, BuildValidationResult]:
        command = input("Enter your command: ") 
        
        if command in POSSIBLE_COMMANDS:
            return (
                command,
                BuildValidationResult.success()
            )
        
        else:
            return (
                command,
                BuildValidationResult.fail("You entered unrecognizable command!")
            )        
        
    def _set(self, obj: Any) -> None:
        if isinstance(obj, str):
            self.context.command = obj
        else:
            raise TypeError(f"{obj} is not of a type str!")
        
    def _validate(self, state: State) -> BuildValidationResult:
        phase_type = state.get_phase_type()
        if self.context.command in VALID_COMMANDS_IN_PHASE[phase_type]:
            return BuildValidationResult.success()
        else:
            return BuildValidationResult.fail(f"Command: {self.context.command} is not usable in phase: {phase_type}")


class SourceBuildStep(BuildStep):

    def _find(self, state: State) -> tuple[Any, BuildValidationResult]:
        key = input("Enter key for source of your action: ")
        obj, success = state.find_obj_by_key(key)

        if success:
            return (
                obj,
                BuildValidationResult.success()
            )
        
        else:
            return (
                obj,
                BuildValidationResult.fail(f"Unrecognizable object for your key: {key}")
            )
    
    def _set(self, obj: Any) -> None:
        self.context.source = obj
    
    def _validate(self) -> BuildValidationResult:
        source = self.context.source
        match self.context.source_type:
            case SourceType.CARD:
                if isinstance(source, Card):
                    return BuildValidationResult.success()
                return BuildValidationResult.fail("Entered source is not of the correct type: Card!")
            
            case SourceType.PLAYER:
                if isinstance(source, Player):
                    return BuildValidationResult.success()
                return BuildValidationResult.fail("Entered source is not of the correct type: Player!")
            
            case SourceType.ABILITY:
                if isinstance(source, AbilityDefinition):
                    return BuildValidationResult.success()
                return BuildValidationResult.fail("Entered source is not of the correct type: AbilityDefintion!")
            
            case _:
                return BuildValidationResult.success()
    

class ModeBuildStep(BuildStep):

    def _find(self, state: State) -> tuple[Any, BuildValidationResult]:
        valid_modes = self.context.action_def.ability_def.get_modes()
        obj = input(f"Enter mode number from 0 to {len(valid_modes - 1)}")
        
        try:
            idx = int(obj)
        except:
            return (obj, BuildValidationResult.fail(f"Input: {obj} was not a number!"))
        
        if idx < 0 or idx >= len(valid_modes):
            return (obj, BuildValidationResult.fail(f"Mode index: {idx} is out of bounds!"))
        
        return (idx, BuildValidationResult.success())
    
    def _set(self, obj: Any) -> None:
        self.context.mode = obj


class CostBuildStep(BuildStep):

    def _find(self, state: State) -> tuple[Any, BuildValidationResult]:
        binding: TargetBinding = dict()
        for key in self.context.paid_costs.keys():
            all_passed = False
            while not all_passed:
                line = input(f"Enter targets for cost effects for cost: {key}: ").split()

                objs: list[Any] = []

                all_passed = True
                for k in line:
                    obj, success = state.find_obj_by_key(k)
                    if not success:
                        all_passed = False
                        break
                    objs.append(obj)
                
                d: dict[Any, int] = dict()
                for o in objs:
                    if d.get(o):
                        d[o] += 1
                    else: 
                        d[o] = 1

                binding[key] = d
        
        return (binding, BuildValidationResult.success())
    
    def _set(self, obj: Any) -> None:
        self.context.cost_binding = obj
    
    def _validate
    




    


class ActionBuilder:

    def __init__(self):

        self.steps = [
            CommandBuildStep(),
            SourceStep(),
            ModeStep(),
            CostStep(),
            TargetStep(),
            FinalizeStep(),
        ]

    def build(
        self,
        player: Player,
        state: State
    ) -> GameAction:

        context = ActionBuildContext(
            player=player,
            state=state
        )

        for step in self.steps:

            if not self._should_run_step(
                step,
                context
            ):
                continue

            step.run(context)

        return context.final_action
    
    def _should_run_step(self, step: BuildingStep, context: ActionBuildContext) -> bool:
        if isinstance(step, ModeStep):
            return context.action_definition.requires_mode

        if isinstance(step, TargetStep):
            return context.action_definition.requires_targets

        if isinstance(step, CostStep):
            return context.action_definition.requires_cost_payment

        return True