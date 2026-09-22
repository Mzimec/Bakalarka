"""Composable candidate generation and decision policies for bounded game search."""
from __future__ import annotations

from game.ai.decision_maker import (
    AbilityDecisionRequest,
    DeclareAttackersRequest,
    DeclareBlockersRequest,
    PriorityDecisionRequest,
)

from game.ai.decision_maker import DecisionResult
from game.game_actions.generation.decision_abstraction.options import (
    DeclareAttackersOption,
    DeclareBlockersOption,
)

from dataclasses import dataclass

from game.ai.simple_agent import SimpleAgent
from game.mana.mana_solver import SourceActivatingManaSolver
from game.enums import ZoneType
from game.game_actions.data_structs.game_action import PassPriorityAction, GameAction


@dataclass
class Candidate:
    """!
    @brief One engine action or declaration with a public description and prior.
    """
    action: object
    score: float
    description: dict
    key: object = None


class HeuristicSelector:
    """!
    @brief Select the highest prior; replace this policy with search or an LLM.
    """
    def choose(self, observation, candidates):
        return max(range(len(candidates)), key=lambda index: candidates[index].score)


def card_view(card, state):
    """!
    @brief Snapshot a visible card without serializing runtime references.
    """
    return {
        "id": card.command_id, "name": card.name,
        "controller": card.get_controller(state).idx,
        "zone": card.get_zone().name,
        "types": sorted(value.name for value in card.get_types(state)),
        "text": card.definition.oracle_text,
        "cost": str(card.get_mana_cost(state)),
        "power": card.get_power(state), "toughness": card.get_toughness(state),
        "tapped": card.is_tapped,
    }


def observation(state, player):
    """!
    @brief Expose own hand and public battlefield, never library order or enemy hand.
    """
    return {
        "turn": state.turn.number, "phase": state.turn.phase.name,
        "self": player.idx, "active": state.active_player.idx,
        "players": [
            {"id": p.idx, "life": p.health, "hand_size": len(p.hand),
             "library_size": len(p.deck),
             "mana": {mana.name: amount for mana, amount in p.mana_pool.items()}}
            for p in state.players
        ],
        "hand": [card_view(c, state) for c in player.hand.values()],
        "battlefield": [
            card_view(c, state) for c in state.get_cards(from_zones=[ZoneType.BATTLEFIELD])
        ],
        "stack": [
            card_view(c, state) for c in state.get_cards(from_zones=[ZoneType.STACK])
        ],
        "pending_abilities": [
            {"ability": item.action_resolution.context.action_key,
             "controller": item.action_resolution.context.controller.idx}
            for item in state.stack.items
        ],
        "combat": {
            "attackers": [c.command_id for c in state.combat.attackers],
            "blockers": {b.command_id: a.command_id for b, a in state.combat.blockers.items()},
        },
    }


class ActionEvaluator:
    """!
    @brief Rank concrete target choices using baseline value and threat size.

    Scores are priors, not predictions from simulated future states.
    """
    def score(self, state, player, action):
        value = SimpleAgent.score(self, state, player, action)
        for target in SimpleAgent._targets(action):
            if hasattr(target, "get_power") and target.get_controller(state) is not player:
                value += max(0, target.get_power(state) or 0)
                value += max(0, target.get_toughness(state) or 0) * 0.5
        return value


class ParameterPolicy:
    """!
    @brief Bound X proposals before legal cost and target generation.

    This is a proposal budget, not a claim that higher X values are illegal.
    """
    def __init__(self, max_x=8):
        if type(max_x) is not int or max_x < 0:
            raise ValueError("Maximum proposed X must be nonnegative.")
        self.max_x = max_x

    def values(self, ability, state):
        cost = str(ability.source.get_mana_cost(state))
        return range(self.max_x, -1, -1) if "X" in cost else (0,)

    def generate(self, ability, state):
        from game.game_actions.generation.decision_abstraction.parameters import AbilityParameters
        for value in self.values(ability, state):
            yield AbilityParameters(x_value=value)


class CandidateGenerator:
    """!
    @brief Bound target/cost search and retain the best concrete actions per ability.

    The mana policy supplies a canonical payment, avoiding equivalent tap-order
    branches. Target and cost plans still come from the engine's public pipeline.
    """
    def __init__(self, *, target_limit=24, cost_limit=4, keep_per_ability=4,
                 mana_solver=None, evaluator=None, parameters=None):
        if any(type(v) is not int or v < 1 for v in
               (target_limit, cost_limit, keep_per_ability)):
            raise ValueError("Candidate budgets must be positive integers.")
        self.target_limit = target_limit
        self.cost_limit = cost_limit
        self.keep_per_ability = keep_per_ability
        self.mana_solver = mana_solver or SourceActivatingManaSolver()
        self.evaluator = evaluator or ActionEvaluator()
        self.parameters = parameters or ParameterPolicy()

    def generate(self, state, player, attempted):
        """!
        @brief Return ranked legal witnesses plus the always-available priority pass.
        """
        from game.game_actions.generation.decision_abstraction.requests import PriorityDecisionRequest
        from game.game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy, PriorityGenerationPolicy
        from game.game_actions.generation.generation_strategy import action_generation_strategy
        from game.game_actions.generation.pruning.pruning_strategy import LimitPruning, FilterPruning
        from game.rules.lands import LandPlayAction

        strategy = action_generation_strategy(
            mana_solver=self.mana_solver,
            cost_plan_pruning=LimitPruning(self.cost_limit),
            action_plan_pruning=LimitPruning(self.target_limit),
        )
        policy = PriorityGenerationPolicy(
            ability_gp=AbilityGenerationPolicy(strategy=strategy, parameter_strategy=self.parameters),
            ability_space_ps=FilterPruning(lambda ability: (ability.source.command_id, ability.key) not in attempted),
            include_mana=False, include_concede=False,
        )
        result, by_ability = [], {}
        for action in PriorityDecisionRequest(state, player).option_space(policy):
            if isinstance(action, PassPriorityAction):
                result.append(Candidate(action, 0, {"kind": "pass"}))
            elif isinstance(action, LandPlayAction):
                result.append(Candidate(action, 100, {"kind": "land", "source": action.card.command_id}))
            else:
                candidate = self._ability_candidate(state, player, action)
                ranked = by_ability.setdefault(candidate.key, [])
                ranked.append(candidate)
                ranked.sort(key=lambda item: -item.score)
                del ranked[self.keep_per_ability:]
        for ranked in by_ability.values():
            result.extend(ranked)
        return result

    def _ability_candidate(self, state, player, action):
        """Agent-owned ranking and presentation of an already generated action."""
        from game.enums import SAVariableType
        cost = action.cost_generator
        payment = cost.mana_solver_result
        return Candidate(
            action, self.evaluator.score(state, player, action),
            {"kind": "action", "source": action.source.command_id,
             "ability": action.action_key,
             "x": cost.param_context.x_variables.get(SAVariableType.X, 0),
             "payment": {
                 "mana": {m.name: n for m, n in (payment.payment or {}).items()},
                 "life": payment.life_payment,
                 "activate": [a.source.command_id for a in payment.mana_plan],
             } if payment else {},
             "cost_targets": [getattr(t, "command_id", None) or t.name
                              for groups in cost.binding.values()
                              for group in groups.values() for t in group],
             "targets": [getattr(t, "command_id", None) or t.name for t in SimpleAgent._targets(action)]},
            (action.source.command_id, action.action_key),
        )


class CombatPolicy:
    """Compose proposals, shared legality pipelines and agent-owned evaluation."""

    def __init__(self, *, max_assignments=512, attackers_strategy=None,
                 blockers_strategy=None, evaluator=None):
        from game.ai.combat_strategies import (
            BoundedBlockersStrategy, ConservativeAttackersStrategy, CombatEvaluator,
        )
        if type(max_assignments) is not int or max_assignments < 1:
            raise ValueError("Combat search budget must be positive.")
        self.max_assignments = max_assignments
        self.evaluator = evaluator or CombatEvaluator()
        self.attackers_strategy = attackers_strategy or ConservativeAttackersStrategy(self.evaluator)
        self.blockers_strategy = blockers_strategy or BoundedBlockersStrategy(max_assignments)

    def attackers(self, state, player):
        from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest
        from game.game_actions.generation.decision_abstraction.policies import DeclareAttackersPolicy
        request = DeclareAttackersRequest(state, player)
        return [Candidate(dict(option.declarations), self.evaluator.attack_score(request, option), {
            "kind": "attack", "attackers": [c.command_id for c in option.declarations],
        }) for option in request.option_space(DeclareAttackersPolicy(strategy=self.attackers_strategy))]

    def blockers(self, state, player):
        from game.game_actions.generation.decision_abstraction.requests import DeclareBlockersRequest
        from game.game_actions.generation.decision_abstraction.policies import DeclareBlockersPolicy
        request = DeclareBlockersRequest(state, player)
        return [Candidate(dict(option.declarations), self.evaluator.block_score(request, option), {
            "kind": "block", "blockers": {b.command_id: a.command_id
                                            for b, a in option.declarations.items()},
        }) for option in request.option_space(DeclareBlockersPolicy(strategy=self.blockers_strategy))]


class ModularAgent(SimpleAgent):
    """!
    @brief Delegate proposal, valuation, payment and combat to replaceable policies.

    Inherited auxiliary decisions provide deterministic mulligans and discards.
    """
    def __init__(self, *, candidates=None, selector=None, combat=None, candidate_limit=32, **kwargs):
        super().__init__(**kwargs)
        if type(candidate_limit) is not int or candidate_limit < 2:
            raise ValueError("Main candidate limit must be at least two.")
        self.candidate_limit = candidate_limit
        self.candidates = candidates or CandidateGenerator()
        self.selector = selector or HeuristicSelector()
        self.combat_policy = combat or CombatPolicy()

    def _choose(self, state, player, candidates):
        if not candidates:
            raise ValueError("No legal decision candidate was generated.")
        # Keep passing available even when pruning low-prior branches.
        passes = [c for c in candidates if c.description.get("kind") == "pass"]
        ranked = sorted((c for c in candidates if c not in passes), key=lambda c: -c.score)
        candidates = passes + ranked[:self.candidate_limit - len(passes)]
        index = self.selector.choose(observation(state, player), candidates)
        if type(index) is not int or not 0 <= index < len(candidates):
            raise ValueError("Decision policy returned an invalid candidate index.")
        selected = candidates[index]
        self._record(state, player, "policy_choice", candidate=index,
                     choice=selected.description, score=selected.score,
                     fallback=getattr(self.selector, "last_error", None))
        return selected

    def decide_priority(self, request: PriorityDecisionRequest) -> DecisionResult[GameAction]:
        """!
        @brief Choose among bounded engine actions without modifying game state.
        """
        state, player = request.state, request.player
        window = (state.turn.number, state.turn.phase)
        if window != self._window:
            self._window = window
            self._attempted.clear()
        candidates = self.candidates.generate(state, player, self._attempted)
        # This is the configured policy's space, not a claim about all legal
        # actions. Do not build an observation or call a model for its sole pass.
        if (self.auto_pass and len(candidates) == 1
                and isinstance(candidates[0].action, PassPriorityAction)):
            return self._auto_pass_result(request, reason="only_pass_candidate")
        selected = self._choose(state, player, candidates)
        if selected.key is not None:
            self._attempted.add(selected.key)
        return DecisionResult(selected.action)

    def decide_attackers(self, request: DeclareAttackersRequest) -> DecisionResult[DeclareAttackersOption]:
        state, player = request.state, request.player
        return DecisionResult(DeclareAttackersOption(self._choose(state, player, self.combat_policy.attackers(state, player)).action))

    def decide_blockers(self, request: DeclareBlockersRequest) -> DecisionResult[DeclareBlockersOption]:
        state, player = request.state, request.player
        return DecisionResult(DeclareBlockersOption(self._choose(state, player, self.combat_policy.blockers(state, player)).action))

    def decide_ability(self, request: AbilityDecisionRequest) -> DecisionResult[GameAction]:
        """!
        @brief Rank required trigger targets through the same main policy.
        """
        state, trigger, choices = request.state, request.ability, request.options
        candidates = [
            Candidate(action, self.candidates.evaluator.score(state, trigger.controller, action),
                      {"kind": "trigger", "ability": trigger.key,
                       "targets": [getattr(t, "command_id", None) or t.name
                                   for t in self._targets(action)]})
            for action in choices
        ]
        return DecisionResult(self._choose(state, trigger.controller, candidates).action)
