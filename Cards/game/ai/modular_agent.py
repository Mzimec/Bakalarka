"""Composable candidate generation and decision policies for bounded game search."""

from dataclasses import dataclass
from itertools import islice, product

from game.ai.simple_agent import SimpleAgent
from game.ai.mana_solver import SourceActivatingManaSolver
from game.console.command_choices import legal_plans, UnsupportedCommandDefinition
from game.enums import CardType, ZoneType
from game.game_actions.data_structs.game_action import PassPriorityAction
from game.rules.lands import LandPlayAction, land_play_error


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
        result = [Candidate(PassPriorityAction(player), 0, {"kind": "pass"})]
        sources = list(player.hand.values()) + [
            c for c in state.get_cards(from_zones=[ZoneType.BATTLEFIELD, ZoneType.GRAVEYARD])
            if c.get_controller(state) is player
        ]
        for card in sources:
            if card.is_type(state, CardType.LAND) and land_play_error(card, player, state) is None:
                result.append(Candidate(LandPlayAction(card, player), 100,
                                        {"kind": "land", "source": card.command_id}))
            for definition in sorted(card.get_ability_defs(state).values(), key=lambda d: d.key):
                key = (card.command_id, definition.key)
                if key in attempted or definition.is_mana_ability:
                    continue
                if definition.validation_error(card, player, state):
                    continue
                ability = definition.to_ability(card, player)
                options = []
                for x_value in self.parameters.values(ability, state):
                    try:
                        costs = list(islice(legal_plans(
                            ability, state, cost=True, mana_solver=self.mana_solver, x_value=x_value
                        ), self.cost_limit))
                        if not costs:
                            continue
                        for _, plan in islice(legal_plans(ability, state, x_value=x_value), self.target_limit):
                            for _, cost in costs:
                                action = ability.to_game_action(cost, plan)
                                options.append(Candidate(
                                    action, self.evaluator.score(state, player, action),
                                    {"kind": "action", "source": card.command_id,
                                     "ability": definition.key, "x": x_value,
                                     "payment": {
                                         "mana": {m.name: n for m, n in
                                                  (cost.mana_solver_result.payment or {}).items()},
                                         "life": cost.mana_solver_result.life_payment,
                                         "activate": [a.source.command_id for a in
                                                      cost.mana_solver_result.mana_plan],
                                     } if cost.mana_solver_result else {},
                                     "cost_targets": [
                                         getattr(t, "command_id", None) or t.name
                                         for groups in cost.binding.values()
                                         for group in groups.values() for t in group
                                     ],
                                     "targets": [
                                         getattr(t, "command_id", None) or t.name
                                         for t in SimpleAgent._targets(action)
                                     ]}, key,
                                ))
                    except UnsupportedCommandDefinition:
                        continue
                result.extend(sorted(options, key=lambda c: -c.score)[:self.keep_per_ability])
        return result


class CombatPolicy:
    """!
    @brief Search bounded blocker assignments and conservative attack alternatives.

    Complete declarations are validated by the engine. The value model estimates
    trades from power/toughness; it does not replace combat damage resolution.
    """
    def __init__(self, *, max_assignments=512):
        if type(max_assignments) is not int or max_assignments < 1:
            raise ValueError("Combat search budget must be positive.")
        self.max_assignments = max_assignments

    def attackers(self, state, player):
        opponent = next(p for p in state.active_players if p is not player)
        available = [c for c in state.combat.legal_attackers(player) if (c.get_power(state) or 0) > 0]
        defenders = [
            c for c in state.get_cards(from_zones=[ZoneType.BATTLEFIELD])
            if c.get_controller(state) is opponent and c.is_type(state, CardType.CREATURE)
            and not c.is_tapped
        ]
        safe = {
            c: opponent for c in available
            if not any(
                (not c.has_keyword(state, "flying")
                 or b.has_keyword(state, "flying") or b.has_keyword(state, "reach"))
                and (b.get_power(state) or 0) >= (c.get_toughness(state) or 0)
                for b in defenders
            )
        }
        candidates = []
        for declaration in ({}, safe, dict.fromkeys(available, opponent)):
            try:
                state.combat.validate_attackers(player, declaration)
            except ValueError:
                continue
            power = sum(c.get_power(state) or 0 for c in declaration)
            losses = sum((c.get_power(state) or 0) + (c.get_toughness(state) or 0)
                         for c in declaration if c not in safe)
            value = power - losses
            if not defenders and power >= opponent.health:
                value += 1000
            candidates.append(Candidate(declaration, value, {
                "kind": "attack", "attackers": [c.command_id for c in declaration]
            }))
        return candidates

    def blockers(self, state, player):
        attackers = list(state.combat.attackers)
        legal = {a: state.combat.legal_blockers(player, a) for a in attackers}
        blockers = list(dict.fromkeys(b for group in legal.values() for b in group))
        choices = [[None] + [a for a in attackers if b in legal[a]] for b in blockers]
        # A baseline declaration supplies a legal witness even when the bounded
        # search cannot reach an assignment satisfying mandatory blocks.
        baseline = SimpleAgent().choose_blockers(state, player)
        declarations = [baseline]
        declarations.extend(
            {b: a for b, a in zip(blockers, assignment) if a is not None}
            for assignment in islice(product(*choices), self.max_assignments)
        )
        result = []
        for declaration in declarations:
            try:
                state.combat.validate_blockers(player, declaration)
            except ValueError:
                continue
            damage = sum(max(0, a.get_power(state) or 0)
                         for a in attackers if a not in declaration.values())
            value = -damage * (8 if damage >= player.health else 1)
            for a in attackers:
                group = [b for b, target in declaration.items() if target is a]
                if not group:
                    continue
                if sum(b.get_power(state) or 0 for b in group) >= (a.get_toughness(state) or 0):
                    value += (a.get_power(state) or 0) + (a.get_toughness(state) or 0)
                for b in group:
                    if (a.get_power(state) or 0) >= (b.get_toughness(state) or 0):
                        value -= (b.get_power(state) or 0) + (b.get_toughness(state) or 0)
            result.append(Candidate(declaration, value, {
                "kind": "block", "blockers": {b.command_id: a.command_id for b, a in declaration.items()}
            }))
        return result


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

    def get_action(self, state, player):
        """!
        @brief Choose among bounded engine actions without modifying game state.
        """
        window = (state.turn.number, state.turn.phase)
        if window != self._window:
            self._window = window
            self._attempted.clear()
        selected = self._choose(
            state, player, self.candidates.generate(state, player, self._attempted)
        )
        if selected.key is not None:
            self._attempted.add(selected.key)
        return selected.action

    def choose_attackers(self, state, player):
        return self._choose(state, player, self.combat_policy.attackers(state, player)).action

    def choose_blockers(self, state, player):
        return self._choose(state, player, self.combat_policy.blockers(state, player)).action

    def choose_trigger_action(self, state, trigger, choices):
        """!
        @brief Rank required trigger targets through the same main policy.
        """
        candidates = [
            Candidate(action, self.candidates.evaluator.score(state, trigger.controller, action),
                      {"kind": "trigger", "ability": trigger.key,
                       "targets": [getattr(t, "command_id", None) or t.name
                                   for t in self._targets(action)]})
            for action in choices
        ]
        return self._choose(state, trigger.controller, candidates).action
