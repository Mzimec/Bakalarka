"""One immutable candidate-generation policy shared by live and replay controllers."""
from dataclasses import dataclass
from functools import cached_property


def search_generation_policy(request):
    """Explicit automatic-payment menu, identical for live and replay players.

Separate mana activations and concession are excluded; no heuristic top-k is
applied. Replay rejects oversized menus; live emergency choices disable further
search in that game and are logged. General parameter/candidate policies
remain a separate extension, not silently borrowed from SimpleAgent.
"""
    from game.ai.agents.decision_maker import PriorityDecisionRequest
    from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy
    if isinstance(request, PriorityDecisionRequest):
        # A private effect in a sampled opponent hand must not toggle a global
        # symmetry guard and change the actor's menu. Search exposes concrete
        # sources until equivalence has an actor-observation proof contract.
        return PriorityGenerationPolicy(include_mana=False, include_concede=False, equivalence=None)
    return None


@dataclass(frozen=True)
class SearchGenerationPolicy:
    staged_priority: bool = False
    staged_ability: bool = False
    progressive_candidates: bool = False
    candidate_initial_width: int = 4
    candidate_beam_width: int = 32
    candidate_rank_window: int = 0
    candidate_rank_windows: tuple = ()
    candidate_native_ordering: bool = False
    candidate_attack_risk: bool = False
    candidate_combat_equivalence: bool = False
    sequential_combat: bool | str = False

    def __post_init__(self):
        if (type(self.sequential_combat) is not bool
                and (type(self.sequential_combat) is not str or self.sequential_combat not in {'attackers', 'blockers'})):
            raise ValueError('sequential_combat must be boolean, attackers or blockers')
        from game.ai.decisions.candidate_ranking import (rank_window, normalize_rank_windows)
        rank_window(self.candidate_rank_window)
        object.__setattr__(self, 'candidate_rank_windows', normalize_rank_windows(self.candidate_rank_windows))

    @cached_property
    def candidate_model(self):
        if not self.progressive_candidates:
            return None
        from game.ai.decisions.generation_candidates import DeterministicCandidates
        return DeterministicCandidates(self.candidate_initial_width, self.candidate_beam_width,
            self.candidate_rank_window, self.candidate_rank_windows, self.candidate_native_ordering,
            self.candidate_attack_risk, self.candidate_combat_equivalence)

    def __call__(self, request):
        from dataclasses import replace
        from game.ai.agents.decision_maker import PriorityDecisionRequest
        policy = search_generation_policy(request)
        if isinstance(request, PriorityDecisionRequest) and self.staged_priority:
            policy = replace(policy, staged_sources=True, staged_ability=self.staged_ability)
        return policy


