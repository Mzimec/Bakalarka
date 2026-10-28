"""Previous/native risk drafts versus full menus; RAM/stdout only, no strength claim."""
import argparse
from statistics import median
from time import perf_counter
import json

from benchmarks.search_tactics import tactical_scenario
from benchmarks.mcts_top_k import make_case
from game.ai.attack_candidates import AttackRiskAssessment
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.modular_agent import ModularAgent
from game.enums import CardType, ZoneType
from game.game_state import Card, CardDefinition
from game.game_actions.generation.decision_abstraction.policies import DeclareAttackersPolicy
from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest


def fixture(case, controllers):
    """Build small exhaustive fixtures and a larger pair-classification stress case."""
    if case == 'unblocked_10':
        return make_case('attackers', controllers)[0]
    game = tactical_scenario('attack_pacified')(controllers)
    state = game.state
    stats = ([(5, 8)] * 2 + [(4, 4)] * 2 + [(2, 2)] * 2 + [(1, 1)] * 2 + [(0, 8)]
             if case == 'mixed_10' else [(3, 5)] * 39)
    for owner, values in ((0, stats), (1, [(2, 2)] * (2 if case == 'mixed_10' else 40))):
        for power, toughness in values:
            player = state.players[owner]
            card = Card(CardDefinition('Unit', types=frozenset({CardType.CREATURE}),
                power=power, toughness=toughness), player)
            player.add_card(card, ZoneType.BATTLEFIELD)
            card.controlled_since = 0
    return game


def measurement(request, variant, width):
    """Measure generation plus the same detached projection, excluding ranking."""
    model = DeterministicCandidates(width, 32, native_ordering=True, attack_risk=variant == 'risk')
    started = perf_counter()
    if variant == 'full':
        options, complete = tuple(request.option_space(DeclareAttackersPolicy())), True
    else:
        frontier = CandidateFrontier(model.generate(request, DeclareAttackersPolicy()), 4096)
        try:
            options, complete = frontier.take(width)
        finally:
            frontier.close()
    info = DecisionProjector(request, options, PlayerRecall()).build().information
    return (perf_counter() - started) * 1000, len(info.actions), complete


def generation(repeats=6, width=4):
    for case in ('unblocked_10', 'mixed_10', 'pairs_40'):
        game = fixture(case, (ModularAgent(), ModularAgent()))
        request = DeclareAttackersRequest(game.state, game.state.players[0])
        variants = ('previous', 'risk') if case == 'pairs_40' else ('full', 'previous', 'risk')
        for variant in variants:
            measurement(request, variant, width)
        samples = {variant: [] for variant in variants}
        offered = {}
        for repeat in range(repeats):
            for variant in variants if repeat % 2 == 0 else reversed(variants):
                elapsed, count, complete = measurement(request, variant, width)
                samples[variant].append(elapsed)
                offered[variant] = count
        assessment = AttackRiskAssessment(game.state)
        opponent = game.state.players[1]
        blockers = game.state.combat.potential_blockers(opponent)
        groups = {}
        for card in game.state.combat.legal_attackers(game.state.players[0]):
            risk = assessment.classify(card, opponent, blockers).name
            groups[risk] = groups.get(risk, 0) + 1
        print(json.dumps(dict(kind='generation', case=case, width=width,
            medians_ms={v: median(samples[v]) for v in variants}, samples_ms=samples,
            offered=offered, groups=groups, pair_checks=assessment.pair_checks)), flush=True)


def searches(repeats=4, iterations=16):
    """Compare budgets on fixed worlds; ordering changes search work and results."""
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.search_simulator import SearchSimulator
    from game.simulation.search_config import SearchGenerationPolicy
    for case in ('unblocked_10', 'mixed_10'):
        for repeat in range(repeats):
            for variant in ('previous', 'risk') if repeat % 2 == 0 else ('risk', 'previous'):
                policy = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True,
                                                candidate_attack_risk=variant == 'risk')
                def factory(seed):
                    return SearchSimulator(lambda controllers: fixture(case, controllers), policy_for=policy,
                        track_information=True, structured_records=True, projection_cache_size=128)
                chooser = ObservationOpponentPolicy()
                search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
                    action_prior=HeuristicActionPrior(chooser), selection='puct', compress_forced=True,
                    require_root_coverage=False, force_initial_coverage=False, widening_constant=4,
                    budget=InformationSearchBudget(worlds=2, iterations=iterations, max_depth=4,
                                                  max_extensions=8, max_advances=4096))
                root = factory(0).start().player_information()
                started = perf_counter()
                result = search.search(factory, root, seed=42)
                seconds = perf_counter() - started
                m = result.metrics
                print(json.dumps(dict(kind='search', case=case, repeat=repeat, variant=variant,
                    seconds=seconds, completed=m.iterations_completed, logical_steps=m.advance_attempts,
                    native_calls=m.simulator_advance_calls, fallback=result.used_fallback,
                    value=result.value, choice=result.choice.describe())), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=6)
    parser.add_argument('--search', action='store_true')
    parser.add_argument('--iterations', type=int, default=16)
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1:
        parser.error('Counts must be positive')
    if args.search:
        searches(args.repeats, args.iterations)
    else:
        generation(args.repeats)


if __name__ == '__main__':
    main()
