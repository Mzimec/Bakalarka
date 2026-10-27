"""Lazy first-candidate baseline for measuring games with minimal policy work."""
from __future__ import annotations

from contextlib import closing

from game.ai.combat_strategies import AllAttackersStrategy
from game.ai.decision_maker import DecisionMaker, DecisionResult
from game.ai.simple_agent import DecisionLimitReached
from game.game_actions.data_structs.game_action import PassPriorityAction
from game.game_actions.generation.decision_abstraction.policies import (
    DeclareAttackersPolicy, PriorityGenerationPolicy,
)
from game.game_actions.generation.decision_abstraction.priority_availability import (
    NON_MANA_PRIORITY_AVAILABILITY,
)
from game.game_actions.generation.decision_abstraction.requests import (
    DeclareAttackersRequest, PriorityDecisionRequest,
)
from game.game_actions.generation.pruning.pruning_strategy import FilterPruning


class TrivialAgent(DecisionMaker):
    """!
    @brief Choose the first generated candidate, without observations or scoring.

    Priority skips pass when a non-mana action exists; mana is paid by the
    normal solver. Combat tries the existing all-attack proposal and otherwise
    the first legal declaration; blockers normally choose no blocks. Other
    requests take their first option (including keeping the opening hand).

    Like SimpleAgent, try each priority ability at most once per turn/phase,
    so a free repeatable ability cannot prevent the game from advancing.
    This changes the game trajectory: timings are a baseline workload, not
    the time the same stronger-agent game would take without its policy.
    """

    def __init__(self, *, max_decisions: int = 10000, auto_pass: bool = True,
                 log=None) -> None:
        if type(max_decisions) is not int or max_decisions < 1:
            raise ValueError("max_decisions must be a positive integer.")
        if type(auto_pass) is not bool:
            raise ValueError("auto_pass must be a boolean.")
        self.max_decisions = max_decisions
        self.auto_pass = auto_pass
        self.decisions = 0
        self.log = log or (lambda *args: None)
        self._window = None
        self._attempted = set()

    @staticmethod
    def _first(space):
        with closing(iter(space)) as options:
            option = next(options, None)
        if option is None:
            raise ValueError("No legal options available for the trivial agent.")
        return option

    def _decide(self, request):
        self.decisions += 1
        if self.decisions > self.max_decisions:
            raise DecisionLimitReached("Decision budget exhausted.")
        if isinstance(request, PriorityDecisionRequest):
            result = self._priority(request)
        elif isinstance(request, DeclareAttackersRequest):
            space = request.option_space(DeclareAttackersPolicy(strategy=AllAttackersStrategy()))
            with closing(iter(space)) as options:
                option = next(options, None)
            # Custom attack restrictions can reject the all-attack proposal.
            result = DecisionResult(option if option is not None else self._first(request.options))
        else:
            result = DecisionResult(self._first(request.options))
        if request.state is not None:
            self.log(request.state, request.player, "trivial_choice", {
                "request": type(request).__name__, "option": type(result.value).__name__,
                "source": getattr(result.value, "source", None),
            })
        return result

    def _priority(self, request):
        state, player = request.state, request.player
        window = (state.turn.number, state.turn.phase)
        if window != self._window:
            self._window = window
            self._attempted.clear()
        prepared = (NON_MANA_PRIORITY_AVAILABILITY.prepare(state, player, self._attempted)
                    if self.auto_pass else None)
        if prepared is not None and prepared.empty:
            return DecisionResult(PassPriorityAction(player), {
                "auto_pass": True, "auto_pass_reason": "no_available_abilities",
            })
        policy = PriorityGenerationPolicy(
            include_mana=False, include_concede=False, collector=prepared,
            ability_space_ps=FilterPruning(
                lambda ability: (ability.source.command_id, ability.key) not in self._attempted),
        )
        with closing(iter(request.option_space(policy))) as options:
            fallback = None
            for action in options:
                if isinstance(action, PassPriorityAction):
                    fallback = action
                    continue
                if hasattr(action, "action_key"):
                    self._attempted.add((action.source.command_id, action.action_key))
                return DecisionResult(action)
        if fallback is None:
            raise ValueError("Priority generation did not supply a pass option.")
        return DecisionResult(fallback, {
            "auto_pass": self.auto_pass,
            "auto_pass_reason": "only_pass_candidate" if self.auto_pass else None,
        })
