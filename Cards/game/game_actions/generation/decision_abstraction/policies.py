"""Each decision owns its generation policy and strategy contract."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from ...data_structs.decision_option import DecisionOption
from ..equivalence import ConservativeEquivalencePolicy, EquivalencePolicy
from .selection_strategies import (
    SelectionStrategy, FullAttackersStrategy, FullBlockersStrategy, FullMulliganStrategy,
    FullMulliganBottomStrategy, FullDiscardStrategy, FullAbilityResolutionStrategy,
)
if TYPE_CHECKING:
    from ..generation_strategy import ActionGenerationStrategy
    from .parameters import AbilityParameterStrategy
    from ....game_state.collectors.priority_ability_collector import PriorityAbilitySource
    from ..pruning.pruning_strategy import PruningStrategy
    from ....mana.mana_solver import ManaSolver
    from .requests import (DeclareAttackersRequest, DeclareBlockersRequest, MulliganRequest,
                           MulliganBottomRequest, DiscardRequest, AbilityResolutionRequest)
    from .options import (DeclareAttackersOption, DeclareBlockersOption, MulliganOption,
                          MulliganBottomOption, DiscardOption, AbilityResolutionOption)


class SelectionPolicy[R, T: DecisionOption](Protocol):
    """!
    @brief Protocol implemented by policies for selection pipelines.
    """
    @property
    def strategy(self) -> SelectionStrategy[R, T]:
        """!
        @brief Return the strategy used by the selection policy.
        @return Selection strategy.
        """
        ...

    @property
    def pruning(self) -> PruningStrategy | None:
        """!
        @brief Return optional pruning applied after legality checks.
        @return Pruning strategy, or `None`.
        """
        ...


class GenerationPolicy:
    """!
    @brief Base marker type for decision-generation policies.
    """
    pass


@dataclass(frozen=True)
class AbilityGenerationPolicy(GenerationPolicy):
    """!
    @brief Policy controlling ability-action generation.
    """
    strategy: ActionGenerationStrategy | None = None
    x_value: int = 0
    life_payment: int | None = None
    pruning: PruningStrategy | None = None
    parameter_strategy: AbilityParameterStrategy | None = None


@dataclass(frozen=True)
class PriorityGenerationPolicy(GenerationPolicy):
    """!
    @brief Policy controlling priority-window action generation.
    """
    ability_gp: AbilityGenerationPolicy = field(default_factory=AbilityGenerationPolicy)
    ability_space_ps: PruningStrategy | None = None
    land_play_ps: PruningStrategy | None = None
    include_mana: bool = True
    include_concede: bool = True
    collector: PriorityAbilitySource | None = None
    equivalence: EquivalencePolicy | None = field(default_factory=ConservativeEquivalencePolicy)


@dataclass(frozen=True)
class SelectionGenerationPolicy(GenerationPolicy):
    """!
    @brief Shared pruning only; use the concrete request policy when generating.
    """
    pruning: PruningStrategy | None = None


@dataclass(frozen=True)
class DeclareAttackersPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for attacker declaration generation.
    """
    strategy: SelectionStrategy[DeclareAttackersRequest, DeclareAttackersOption] = field(default_factory=FullAttackersStrategy)


@dataclass(frozen=True)
class DeclareBlockersPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for blocker declaration generation.
    """
    strategy: SelectionStrategy[DeclareBlockersRequest, DeclareBlockersOption] = field(default_factory=FullBlockersStrategy)


@dataclass(frozen=True)
class MulliganPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for mulligan decisions.
    """
    strategy: SelectionStrategy[MulliganRequest, MulliganOption] = field(default_factory=FullMulliganStrategy)


@dataclass(frozen=True)
class MulliganBottomPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for choosing cards put on bottom after mulligan.
    """
    strategy: SelectionStrategy[MulliganBottomRequest, MulliganBottomOption] = field(default_factory=FullMulliganBottomStrategy)


@dataclass(frozen=True)
class DiscardPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for discard choices.
    """
    strategy: SelectionStrategy[DiscardRequest, DiscardOption] = field(default_factory=FullDiscardStrategy)


@dataclass(frozen=True)
class AbilityResolutionPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for choices made while resolving an ability.
    """
    strategy: SelectionStrategy[AbilityResolutionRequest, AbilityResolutionOption] = field(default_factory=FullAbilityResolutionStrategy)


@dataclass(frozen=True)
class ManaGenerationPolicy(GenerationPolicy):
    """!
    @brief Policy controlling mana-plan generation.
    """
    solver: ManaSolver | None = None


__all__ = [
    "GenerationPolicy", "AbilityGenerationPolicy", "PriorityGenerationPolicy",
    "SelectionGenerationPolicy", "SelectionPolicy", "DeclareAttackersPolicy",
    "DeclareBlockersPolicy", "MulliganPolicy", "MulliganBottomPolicy",
    "DiscardPolicy", "AbilityResolutionPolicy", "ManaGenerationPolicy",
]
