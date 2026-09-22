"""Player actions, scheduled resolutions and operation-generating plans."""

from __future__ import annotations

from dataclasses import dataclass, replace, field
from abc import abstractmethod, ABC
from typing import TYPE_CHECKING
from collections.abc import Generator
from helper.runtime_object import RuntimeObject
from .decision_option import DecisionOption
from ...operations import ConcedeOperation, PassPriorityOperation

if TYPE_CHECKING:
    from ...game_state import Card, State, Player
    from ...operations import Operation
    from ...target import TargetBinding
    from ...target.target_resolver import (
        TargetBinding,
        ImmutableTargetBinding,
        RepetitionTargetSlotWrapper,
    )
    from .ability import EffectSequence, AbilityDefinition, SubAbilityDefinition
    from ...mana.mana_solver import ManaSolverResult

from ...abilities.parameter_context import ParameterContext

__all__ = [
    "ResolutionContext",
    "ExecutionPlan",
    "FixedExecutionPlan",
    "AbilityExecutionPlan",
    "ScheduledResolution",
    "GameAction",
    "AbilityAction",
    "PassPriorityAction",
    "ConcedeAction",
]


@dataclass(frozen=True)
class ResolutionContext:
    """!
    @brief Information shared with operations while a game action is resolving.
    """

    controller: Player | None = None
    source: Card | None = None
    ability: AbilityDefinition | None = None
    subability: SubAbilityDefinition | None = None
    action_key: str = ""
    uses_stack: bool = False
    targets: ImmutableTargetBinding | None = None
    effects: EffectSequence | None = None
    param_ctx: ParameterContext = field(default_factory=ParameterContext)
    is_cost: bool = False
    trigger_event: object | None = None
    trigger_registration: object | None = None
    # Immutable event-time metadata travels with the stack item, independently
    # of the mutable card object and its current zone incarnation.
    source_revision: int | None = None
    source_last_known: object | None = None

    def source_information(self, state):
        """!
        @brief Read current source characteristics or the departed incarnation's LKI.
        @param state Current game state at resolution time.
        @return Source snapshot, or None for a source-less effect.
        """
        from ..resolution.event_bus import capture_single_card

        if self.source is None:
            return None
        revision = self.source_revision
        if revision is not None and self.ability is not None and self.ability.is_spell and not self.is_cost:
            # Casting follows the hand-to-stack move. A resolving spell uses
            # its stack incarnation, including changes made while on the stack.
            revision += 1
        if revision is None or self.source.zone_revision == revision:
            return capture_single_card(state, self.source)
        return getattr(self.source, "_incarnation_history", {}).get(
            revision, self.source_last_known
        )

    def matches_source_incarnation(self, zone_changes: int = 0) -> bool:
        """!
        @brief Check that a source-relative effect still refers to its object.
        @param zone_changes Explicit zone transitions followed by this effect.
        @return Whether the live card has the expected incarnation revision.

        Most effects use zero. A dies ability returning its source may follow
        the one transition that triggered it, but not later exile/return moves.
        Hand-built contexts without a captured revision retain their old API.
        """
        return self.source is not None and (
            self.source_revision is None
            or self.source.zone_revision == self.source_revision + zone_changes
        )


class ExecutionPlan(ABC):
    """!
    @brief Base object that turns an action intent into executable operations.
    """

    @abstractmethod
    def to_operations(
        self, state: State, context: ResolutionContext
    ) -> Generator[Operation, None, None]:
        """!
        @brief Create operations for the current state and resolution context.
        @param state Current game state.
        @param context Resolution metadata for the action.
        @return Operations that should be executed for this generator.
        """
        ...


@dataclass(frozen=True)
class FixedExecutionPlan(ExecutionPlan):
    """!
    @brief Operation generator that always returns the same operations.
    """

    operations: list[Operation]

    def to_operations(
        self, state: State, context: ResolutionContext
    ) -> Generator[Operation, None, None]:
        """!
        @brief Return the fixed operation list as an immutable tuple.
        @param state Current game state.
        @param context Resolution metadata for the action.
        @return Fixed operations stored by this generator.
        """
        for o in self.operations:
            yield o


@dataclass(frozen=True)
class AbilityExecutionPlan(ExecutionPlan):
    """!
    @brief Creates operations from resolved effect bindings and target choices.
    """

    effects: EffectSequence
    binding: ImmutableTargetBinding
    param_context: ParameterContext | None = None
    mana_solver_result: ManaSolverResult | None = None

    def to_operations(
        self, state: State, context: ResolutionContext
    ) -> Generator[Operation, None, None]:
        """!
        @brief Generate operations for each effect with only its relevant targets.
        @param state Current game state.
        @param context Base resolution metadata for the action.
        @return Operations generated by all bound effects.
        """
        if self.mana_solver_result:
            for mana_action in self.mana_solver_result.mana_plan:
                error = mana_action.validation_error(state)
                if error:
                    from ..resolution.cost_transaction import CostPaymentError

                    raise CostPaymentError(error)
                for intent in mana_action.get_intents():
                    yield from intent.generate_operations(state)
                from ..mana_activation import ManaActivationEventOperation

                yield ManaActivationEventOperation(intent.context)
            if self.mana_solver_result.payment:
                from ..mana_effects import SpendManaOperation

                yield SpendManaOperation(context, self.mana_solver_result.payment)
            if self.mana_solver_result.life_payment:
                from ..mana_effects import PayLifeOperation

                yield PayLifeOperation(context, self.mana_solver_result.life_payment)

        for eb in self.effects.sequence:
            targets = self._scope_binding(eb.slots)
            n_context = replace(context, targets=targets)

            yield from eb.effect.to_operations(state, n_context)

    def _scope_binding(
        self, slots: frozenset[RepetitionTargetSlotWrapper]
    ) -> ImmutableTargetBinding:
        """!
        @brief Keep only the target slots needed by one effect.
        @param slots Slot keys required by one effect.
        @return Target binding limited to those slots.
        """
        from ...target.target_resolver import TargetBinding

        scoped = TargetBinding()
        for wrapper in slots:
            chosen = self.binding.get(wrapper.slot.key, {}).get(wrapper.runtime_key)
            if chosen is not None:
                scoped.setdefault(wrapper.slot.key, {})[wrapper.runtime_key] = chosen
        return scoped.to_immutable()


@dataclass(frozen=True)
class ScheduledResolution:
    """!
    @brief A single resolvable part of a game action with its context.
    """

    generator: ExecutionPlan
    context: ResolutionContext

    def __post_init__(self):
        if isinstance(self.generator, AbilityExecutionPlan):
            object.__setattr__(
                self,
                "context",
                replace(
                    self.context,
                    targets=self.generator.binding,
                    effects=self.generator.effects,
                    param_ctx=self.generator.param_context or self.context.param_ctx,
                ),
            )

    def generate_operations(self, state: State) -> Generator[Operation, None, None]:
        """!
        @brief Ask the generator to materialize the operations for this intent.
        @param state Current game state.
        @return Operations to execute for this intent.
        """
        if self.context.is_cost and getattr(self.context.ability, "loyalty_cost", None) is not None:
            from game.rules.permanents import LoyaltyCostOperation

            yield LoyaltyCostOperation(self.context, self.context.ability.loyalty_cost)
        yield from self.generator.to_operations(state, self.context)

    def to_stack_item(self) -> StackItem:
        return StackItem(self)


@dataclass(eq=False)
class StackItem(RuntimeObject):

    action_resolution: ScheduledResolution

    def __post_init__(self) -> None:
        RuntimeObject.__init__(self)

    @property
    def key(self) -> str:
        return self.action_resolution.context.action_key


class GameAction(DecisionOption, ABC):
    """!
    @brief Base command chosen by a player or generated by game rules.
    """

    @abstractmethod
    def get_intents(self) -> tuple[ScheduledResolution, ...]:
        """!
        @brief Split the action into one or more intents that can be executed.
        @return Ordered intents produced by this action.
        """
        ...


@dataclass(frozen=True)
class AbilityAction(GameAction):
    """!
    @brief Game action produced by activating or casting an ability.
    """

    action_key: str
    source: Card
    action_generator: ExecutionPlan
    cost_generator: ExecutionPlan
    uses_stack: bool = True
    controller: Player | None = None

    ability: AbilityDefinition | None = None
    trigger_event: object | None = None

    trigger_registration: object | None = None
    trigger_source_revision: int | None = None
    trigger_source_last_known: object | None = None

    source_revision: int | None = field(default=None, init=False)
    source_had_ability: bool = field(default=False, init=False)

    def __post_init__(self):
        object.__setattr__(self, "source_revision", getattr(self.source, "zone_revision", None))
        state = getattr(self.source, "_game_state", None)
        if self.ability is not None and state is not None:
            object.__setattr__(
                self,
                "source_had_ability",
                self.source.get_ability_defs(state).get(self.ability.key) == self.ability,
            )

    def validation_error(self, state):
        """!
        @brief Return a validation message when the requested action is not legal.
        """
        if self.trigger_event is not None:
            return None
        if self.source_revision is not None and self.source.zone_revision != self.source_revision:
            return "The source changed zones after this action was selected."
        player = self.controller or self.source.owner
        if hasattr(state, "priority") and state.priority.current_player is not player:
            return "You do not have priority."
        if getattr(state, "is_game_over", False):
            return "The game has ended."
        if self.source_had_ability:
            state.refresh_continuous_effects()
            if self.source.get_ability_defs(state).get(self.ability.key) != self.ability:
                return "The source no longer has the selected ability."
        if self.ability is not None:
            return self.ability.validation_error(self.source, player, state)
        return None

    def get_intents(self):
        """!
        @brief Return separate intents for paying the cost and resolving the effect.
        @return Cost intent followed by ability effect intent.
        """
        cost_context = ResolutionContext(
            controller=self.controller if self.controller is not None else self.source.owner,
            source=self.source,
            ability=self.ability,
            action_key=self.action_key,
            is_cost=True,
            trigger_event=self.trigger_event,
            trigger_registration=self.trigger_registration,
            source_revision=(
                self.trigger_source_revision
                if self.trigger_event is not None
                else self.source_revision
            ),
            source_last_known=self.trigger_source_last_known,
        )

        context = replace(cost_context, uses_stack=self.uses_stack, is_cost=False)

        return (
            ScheduledResolution(generator=self.cost_generator, context=cost_context),
            ScheduledResolution(generator=self.action_generator, context=context),
        )


@dataclass(frozen=True)
class ManaAbilityAction(AbilityAction):

    uses_stack: bool = False


@dataclass(frozen=True)
class PassPriorityAction(GameAction):
    """!
    @brief Action representing a player passing priority.
    """

    player: Player

    def get_intents(self):
        """!
        @brief Create the intent that records the priority pass.
        @return Intent for the priority pass operation.
        """
        context = ResolutionContext(controller=self.player)

        return (
            ScheduledResolution(
                generator=FixedExecutionPlan([PassPriorityOperation(context)]), context=context
            ),
        )


@dataclass(frozen=True)
class ConcedeAction(GameAction):
    """!
    @brief Action representing a player conceding the game.
    """

    player: Player

    def get_intents(self) -> tuple[ScheduledResolution, ...]:
        """!
        @brief Create the intent that resolves the concession.
        @return Intent for the concession operation.
        """
        context = ResolutionContext(controller=self.player)

        return (
            ScheduledResolution(
                generator=FixedExecutionPlan([ConcedeOperation(context)]), context=context
            ),
        )


# Names used by the pre-refactor console interface.  They refer to the same
# execution-plan objects, so old callers and the new resolution pipeline stay
# interoperable.
OperationGenerator = ExecutionPlan
FixedOperationGenerator = FixedExecutionPlan
AbilityOperationGenerator = AbilityExecutionPlan
