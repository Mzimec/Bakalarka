"""Memory-free, synchronous full-rules execution of one sampled world.

Only a native decision callback may inspect state. No state/request/option can
escape the callback into a tree. The world is restored after native unwind.
"""
from collections import Counter
from dataclasses import dataclass
from itertools import islice
from math import tanh
from types import SimpleNamespace

from game.ai.agents.decision_maker import (DecisionMaker, DecisionResult)
from game.ai.decisions.search_descriptors import ChoiceDescriptor
from game.ai.simulation.search_simulator import (
    SimulationBudgetExceeded, SimulationHistoryUnavailable, ReplayDiverged,
)
from game.enums import CardType
from game.game_state.turn_history import TurnHistoryUnavailable
from game.stat_type import STAT_KEYWORDS, STAT_STATIC_ABILITIES


class FinishedTrajectory(BaseException):
    def __init__(self, value, *, terminal=False):
        self.value, self.terminal = value, terminal


class InterruptedTrajectory(BaseException):
    def __init__(self, reason):
        self.reason = reason


class RuntimeStateValue:
    """The native material heuristic, read directly from the complete runtime.

Uses the same coefficients as StateValueEvaluator; visibility and opponent
knowledge are intentionally absent. This is a perfect-information evaluator.
"""
    def permanent_value(self, card, state):
        types, keywords = card.get_types(state), card.get_stat(STAT_KEYWORDS, state)
        value = 0.5 + (1.0 if CardType.LAND in types else 0.0)
        if CardType.CREATURE in types:
            power = max(0, card.get_power(state) or 0)
            toughness = max(0, (card.get_toughness(state) or 0) - card.state.damage_marked)
            if "can't attack" not in keywords and 'defender' not in keywords:
                value += 0.6 * power + 0.15 * toughness
            if "can't block" not in keywords:
                value += 0.3 * power + 0.15 * toughness
        value += 0.5 * sum(not a.key.startswith('cast:') for a in card.get_ability_defs(state).values())
        value += 0.5 * len(card.get_trigger_defs(state))
        value += 0.5 * len(card.get_stat(STAT_STATIC_ABILITIES, state) or ())
        return value

    def evaluate(self, state, observer):
        state.refresh_continuous_effects()
        players = state.players
        survivors = [p for p in players if not p.has_lost and p.health > 0 and p.poison_counters < 10]
        if not survivors:
            return 0.0
        if not any(p.idx == observer for p in survivors):
            return -1.0
        if len(survivors) == 1:
            return 1.0
        opponents = len(players) - 1
        score = 0.0
        for player in players:
            sign = 1.0 if player.idx == observer else -1.0 / opponents
            score += sign * (0.1 * player.health - 0.6 * player.poison_counters
                + 0.8 * len(player.hand) + 0.1 * sum(player.mana_pool.values()))
            for card in player.battlefield.values():
                sign = 1.0 if card.get_controller(state).idx == observer else -1.0 / opponents
                score += sign * self.permanent_value(card, state)
        return tanh(score / 15.0)


def settled_priority(request):
    state = request.state
    combat = state.combat
    return (type(request).__name__ == 'PriorityDecisionRequest' and not state.stack.items
        and not (combat.active and combat.attackers and not combat._regular_damage_done))


@dataclass(frozen=True)
class NativeMenu:
    options: tuple
    descriptors: tuple
    complete: bool


class NativeDecision:
    """Lazy native menu. Valid only inside the corresponding controller call."""
    def __init__(self, request, world):
        self.request, self.world = request, world
        self.kind, self.actor = type(request).__name__, request.player.idx
        self.header = self.kind, self.actor, request.state.turn.number, request.state.turn.phase.name
        self.frontier = None
        self.current = None

    def menu(self, width):
        if self.current is not None and (self.current.complete or len(self.current.options) >= width):
            return self.current
        model = getattr(self.world.policy_for, 'candidate_model', None)
        if model is None:
            options = tuple(islice(self.request.option_space(self.world.policy_for(self.request)),
                                   self.world.max_options + 1))
            if len(options) > self.world.max_options:
                raise SimulationBudgetExceeded('Native menu exceeds max_options')
            complete = True
        else:
            from game.ai.decisions.generation_candidates import CandidateFrontier
            if self.frontier is None:
                self.frontier = CandidateFrontier(model.generate(self.request, self.world.policy_for(self.request)),
                                                  self.world.max_options)
            options, complete = self.frontier.take(min(width, self.world.max_options))
        if not options:
            raise ReplayDiverged('Native decision has no legal options')
        unique = {}
        for option in options:
            unique.setdefault(ChoiceDescriptor.from_option(option), option)
        descriptors, options = tuple(unique), tuple(unique.values())
        self.current = NativeMenu(options, descriptors, complete)
        self.world.counts['menus'] += 1
        self.world.counts['descriptors'] += len(descriptors)
        return self.current

    def close(self):
        if self.frontier is not None:
            self.frontier.close()
        self.request = self.current = self.frontier = None


class PerfectInformationController(DecisionMaker):
    """No PlayerKnowledge, PlayerRecall, event receiver or live timing hooks."""
    def __init__(self, world, choose):
        self.world, self.choose = world, choose

    @property
    def sequential_combat(self):
        return getattr(self.world.policy_for, 'sequential_combat', False)

    def decide(self, request):
        return self._validated_decision(request)

    def _decide(self, request):
        self.world.counts['decisions'] += 1
        self.world.run_decisions += 1
        if self.world.run_decisions > self.world.max_decisions:
            raise InterruptedTrajectory('decision_budget')
        decision = NativeDecision(request, self.world)
        try:
            return DecisionResult(self.choose(decision))
        finally:
            decision.close()


class PerfectInformationWorld:
    def __init__(self, factory, policy_for, *, observer, max_options=4096, max_decisions=2048,
                 max_phases=512, evaluator=None):
        self.factory, self.policy_for, self.observer = factory, policy_for, observer
        self.max_options, self.max_decisions, self.max_phases = max_options, max_decisions, max_phases
        self.evaluator = evaluator if evaluator is not None else RuntimeStateValue()
        self.counts, self.run_decisions = Counter(), 0

    def run(self, choose):
        replay = SimpleNamespace(_borrowed_world=False)
        controllers = tuple(PerfectInformationController(self, choose) for _ in range(2))
        self.run_decisions = 0
        try:
            with self.factory.replay_scope(replay):
                game = self.factory(controllers)
                if tuple(p.controller for p in game.state.players) != controllers:
                    raise ValueError('World did not install its private controllers')
                for _ in range(self.max_phases):
                    if game.state.is_game_over:
                        return self.evaluator.evaluate(game.state, self.observer), True
                    game.loop.step(game.state)
                raise InterruptedTrajectory('phase_budget')
        except FinishedTrajectory as outcome:
            return outcome.value, outcome.terminal
        except TurnHistoryUnavailable as error:
            raise SimulationHistoryUnavailable(str(error)) from error
