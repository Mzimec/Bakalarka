from __future__ import annotations

from dataclasses import dataclass, replace
from abc import abstractmethod, ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..game_state import Card, State, Player
    from ..operations import Operation
    from ..target import TargetBinding
    from ..abilities.ability import EffectSequence

__all__ = [
    "ResolutionContext",
    "OperationGenerator",
    "FixedOperationGenerator",
    "AbilityOperationGenerator",
    "ActionIntent",
    "GameAction",
    "AbilityAction",
    "PassPriorityAction",
    "ConcedeAction",
    "DeclareAttackerAction",
    "RemoveAttackerAction",
    "DeclareBlockerAction",
    "RemoveBlockerAction",
    "MuliganAction",
]


@dataclass(frozen=True)
class ResolutionContext:

    controller: Player | None = None
    source: Card | None = None
    action_key: str | None = None
    uses_stack: bool = False
    targets: TargetBinding | None = None


class OperationGenerator(ABC):

    @abstractmethod
    def to_operations(self, state: State, context: ResolutionContext) -> tuple[Operation,...]:
        pass


@dataclass(frozen=True)
class FixedOperationGenerator(OperationGenerator):

    operations: list[Operation]

    def to_operations(self, state: State, context: ResolutionContext) -> tuple[Operation,...]:
        return tuple(self.operations)
    

@dataclass(frozen=True)
class AbilityOperationGenerator(OperationGenerator):

    effects: EffectSequence
    binding: TargetBinding

    def to_operations(self, state: State, context: ResolutionContext) -> tuple[Operation,...]:
        operations: list[Operation] = [] 

        for e in self.effects:
            targets = self._scope_binding(e.slots)
            n_context = replace(context, targets=targets)
            
            operations.extend(e.effect.to_operations(state, n_context))
        
        return tuple(operations)

    
    def _scope_binding(self, slots: set[str]) -> TargetBinding:
        return {k: self.binding.get(k) for k in slots}

    

@dataclass(frozen=True)
class ActionIntent:

    generator: OperationGenerator
    context: ResolutionContext

    def generate_operations(self, state: State) -> tuple[Operation, ...]:
        return self.generator.to_operations(state, self.context)


class GameAction(ABC):

    @abstractmethod
    def get_intents(self) -> list[ActionIntent]:
        pass


@dataclass(frozen=True)
class AbilityAction(GameAction):

    action_key: str
    source: Card
    action_generator: OperationGenerator
    cost_generator: OperationGenerator
    uses_stack: bool

    def get_intents(self) -> list[ActionIntent]:
        cost_context = ResolutionContext(
            controller=self.source.owner,
            source=self.source
        )

        context = replace(cost_context, uses_stack=self.uses_stack)

        return [
            ActionIntent(
                generator=self.cost_generator,
                context=cost_context
            ),
            ActionIntent(
                generator=self.action_generator,
                context=context
            )
        ]


@dataclass(frozen=True)
class PassPriorityAction(GameAction):

    player: Player
    
    def get_intents(self) -> list[ActionIntent]:
        context=ResolutionContext(
            controller=self.player
        )

        return [
            ActionIntent(
                generator=FixedOperationGenerator([PassPriorityOperation()]),
                context=context
            )
        ]
        
    
@dataclass(frozen=True)
class ConcedeAction(GameAction):

    player: Player

    def get_intents(self) -> list[ActionIntent]:
        context=ResolutionContext(
            controller=self.player
        )

        return [
            ActionIntent(
                generator=FixedOperationGenerator([ConcedeOperation()]),
                context=context
            )
        ]
    

@dataclass(frozen=True)
class DeclareAttackerAction(GameAction):

    attacker: Card
    
    def get_intents(self) -> list[ActionIntent]:
        context=ResolutionContext(
            controller=self.attacker.owner,
            source=self.attacker
        )

        return [
            ActionIntent(
                generator=FixedOperationGenerator([DeclareAttackerOperation()]),
                context=context
            )
        ]


@dataclass(frozen=True)
class RemoveAttackerAction(GameAction):

    attacker: Card

    def get_intents(self) -> list[ActionIntent]:
        context=ResolutionContext(
            controller=self.attacker.owner,
            source=self.attacker
        )

        return [
            ActionIntent(
                generator=FixedOperationGenerator([RemoveAttackerOperation()]),
                context=context
            )
        ]


@dataclass(frozen=True)
class DeclareBlockerAction(GameAction):

    blocker: Card

    def get_intents(self) -> list[ActionIntent]:
        context=ResolutionContext(
            controller=self.blocker.owner,
            source=self.blocker
        )

        return [
            ActionIntent(
                generator=FixedOperationGenerator([DeclareBlockerOperation()]),
                context=context
            )
        ]


@dataclass(frozen=True)
class RemoveBlockerAction(GameAction):

    blocker: Card

    def get_intents(self) -> list[ActionIntent]:
        context=ResolutionContext(
            controller=self.blocker.owner,
            source=self.blocker
        )

        return [
            ActionIntent(
                generator=FixedOperationGenerator([RemoveBlockerOperation()]),
                context=context
            )
        ]


@dataclass(frozen=True)
class MuliganAction(GameAction):

    player: Player

    def get_intents(self) -> list[ActionIntent]:
        context = ResolutionContext(controller=self.player)
        return [
            ActionIntent(
                generator=FixedOperationGenerator([MuliganOperation(context)]),
                context=context
            )
        ]
