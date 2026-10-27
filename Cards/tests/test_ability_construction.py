"""Legality, isolation and search budgets of staged ability construction."""
from dataclasses import replace
from itertools import combinations, combinations_with_replacement

import pytest

from benchmarks.search_tactics import tactical_scenario
from game.ai.modular_agent import ModularAgent
from game.ai.decision_maker import DecisionMaker, DecisionResult, PriorityDecisionRequest
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import SearchSimulator
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy
from game.game_actions.generation.decision_abstraction.priority_frames import PrioritySourceOption
from game.game_actions.generation.decision_abstraction.ability_frames import (
    AbilityConstructionFrame, AbilityConstructionOption, AbilityConstructionRequest,
)
from game.simulation.search_config import SearchGenerationPolicy, SearchAgentConfig
from game.target.target_selector import MinMaxTegetSelector


def fixture(case):
    game = tactical_scenario(case)((ModularAgent(), ModularAgent()))
    game.state.priority.current_player = player = game.state.players[0]
    return game, PriorityDecisionRequest(game.state, player)


def expand(frame, state, depth=0):
    assert depth < 32
    for option in frame.options(state):
        if type(option) is AbilityConstructionOption:
            with pytest.raises(ValueError, match="completed"):
                option.get_intents()
            children = list(expand(option._next_frame, state, depth + 1))
            assert ChoiceDescriptor.from_option(option.witness) in set(map(ChoiceDescriptor.from_option, children))
            yield from children
        else:
            yield option


@pytest.mark.parametrize("case", ["lethal_shock", "answered_shock", "useful_pacifism", "redundant_pacifism"])
def test_full_expansion_preserves_exact_legal_actions_and_state(case):
    game, request = fixture(case)
    policy = PriorityGenerationPolicy(include_mana=False, include_concede=False, equivalence=None)
    before = (game.state.rng.getstate(), tuple(request.player.hand.values()),
              tuple(c.is_tapped for c in request.player.battlefield.values()), game.state.players[1].health)
    flat = tuple(request.option_space(policy))
    staged = tuple(request.option_space(replace(policy, staged_sources=True, staged_ability=True)))
    expanded = [leaf for option in staged for leaf in (
        expand(option._construction, game.state) if type(option) is PrioritySourceOption else (option,))]
    descriptors = list(map(ChoiceDescriptor.from_option, expanded))
    assert set(descriptors) == set(map(ChoiceDescriptor.from_option, flat))
    assert len(descriptors) == len(set(descriptors))
    assert before == (game.state.rng.getstate(), tuple(request.player.hand.values()),
                      tuple(c.is_tapped for c in request.player.battlefield.values()), game.state.players[1].health)
    assert getattr(game.state, "_mana_discovery_context", None) is None


@pytest.mark.parametrize("minimum,maximum,repeat", [(0, 3, False), (1, 3, True), (2, 2, False)])
def test_native_target_prefixes_partition_the_original_combinations(minimum, maximum, repeat):
    from collections import Counter
    from game.target.target_resolver import TargetOption
    candidates = tuple(range(4))
    selector = MinMaxTegetSelector(minimum, maximum, repeat)
    combos = combinations_with_replacement if repeat else combinations
    expected = [TargetOption(Counter(group)) for size in range(minimum, maximum + 1)
                for group in combos(candidates, size)]
    assert list(selector.generate_target_options(candidates)) == expected
    for prefix in ((0,), (1, 2), (2, 2)):
        if not repeat and len(set(prefix)) != len(prefix):
            assert not list(selector.generate_completions(candidates, prefix))
            continue
        matching = []
        for group in expected:
            ordered = tuple(c for c in candidates for _ in range(group.get(c, 0)))
            if ordered[:len(prefix)] == prefix:
                matching.append(group)
        assert list(selector.generate_completions(candidates, prefix)) == matching


def test_controller_returns_only_completed_action_and_does_not_pay_during_prefixes():
    class Chooser(DecisionMaker):
        def __init__(self):
            self.seen = []
        def _decide(self, request):
            self.seen.append(type(request).__name__)
            options = tuple(request.option_space(SearchGenerationPolicy(True, True)(request)))
            assert not request.state.stack.items
            assert not any(c.is_tapped for c in request.player.battlefield.values())
            return DecisionResult(next((o for o in options if type(o) is PrioritySourceOption), options[-1]))
    chooser = Chooser()
    game = tactical_scenario("lethal_shock")((chooser, Chooser()))
    game.state.priority.current_player = game.state.players[0]
    result = chooser.decide(PriorityDecisionRequest(game.state, game.state.players[0]))
    assert type(result.value).__name__ == "AbilityAction"
    assert chooser.seen == ["PriorityDecisionRequest", "AbilityConstructionRequest"]


@pytest.mark.parametrize("selection", ["uct", "puct", "hierarchical_puct"])
def test_prefixes_charge_advances_but_completed_ability_consumes_one_depth(selection):
    policy = SearchGenerationPolicy(True, True)
    def factory(seed):
        return SearchSimulator(tactical_scenario("lethal_shock"), policy_for=policy, track_information=True)
    sim = factory(0)
    root = sim.start()
    action = next(a for a in root.player_information().actions if a.kind == "PrioritySourceOption")
    child = sim.advance_action(root, action)
    assert child.decision.kind == "AbilityConstructionRequest"
    assert sim.fork(child) == child
    assert child.observation(0)["pending_abilities"] == []
    result = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(), selection=selection,
        rollout_policy=ObservationOpponentPolicy(), compress_forced=True,
        budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=1, max_advances=256)
    ).search(factory, root.player_information(), seed=42)
    assert not result.used_fallback and result.choice.kind == "PrioritySourceOption"
    assert result.value > 0
    assert result.metrics.advance_attempts > result.metrics.iterations_completed


@pytest.mark.parametrize("changes", [{"staged_ability": True}, {"staged_ability": 1, "staged_priority": True}])
def test_ability_stage_configuration_is_explicit(changes):
    with pytest.raises(ValueError, match="staged_ability"):
        SearchAgentConfig(deck_knowledge="both_lists", **changes)


@pytest.mark.parametrize("minimum,maximum,repeat,distinct", [
    (1, 1, False, False), (0, 2, False, False), (2, 3, True, False), (1, 1, False, True),
])
def test_multislot_modal_cost_and_effect_expansion(minimum, maximum, repeat, distinct):
    from dummy_classes import DummyEffect
    from helper_funcs import make_slot
    from test_ability_pipeline import setup_ability, node
    from game.game_actions.data_structs.action_node import AndActionNode, OrActionNode
    from game.game_actions.data_structs.ability import SubAbilityDefinition, ActivatedAbilityDefinition
    from game.game_actions.generation.generation_strategy import action_generation_strategy
    from game.game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy
    from game.game_state import Card, CardDefinition
    from game.enums import CardType, ZoneType
    targets = []
    a = make_slot("a", targets, frozenset({"b"}) if distinct else frozenset())
    a = replace(a, target_resolver=replace(a.target_resolver,
        target_selector=MinMaxTegetSelector(minimum, maximum, repeat)))
    b = make_slot("b", targets)
    damage, heal = DummyEffect("damage"), DummyEffect("heal")
    graph = OrActionNode((AndActionNode((node("damage", "a"), node("heal", "b"))), node("heal", "a")))
    state, ability = setup_ability(graph, (damage, heal), (a, b))
    for i in range(3):
        target = Card(CardDefinition(str(i), types=frozenset({CardType.CREATURE}), power=1, toughness=1), ability.controller)
        ability.controller.add_card(target, ZoneType.BATTLEFIELD)
        targets.append(target)
    # A separate modal cost side gives the Cartesian product three independent
    # dimensions. It must be validated before a prefix is offered.
    costs = OrActionNode((node("damage", "a"), node("heal", "b")))
    definition = ActivatedAbilityDefinition(cost_subdefs=(SubAbilityDefinition(action_node=costs,
        effects=frozenset({damage, heal}), slots=frozenset({a, b})),),
        action_subdefs=ability.definition.action_subdefs)
    ability = definition.to_ability(ability.source, ability.controller)
    policy = AbilityGenerationPolicy(strategy=action_generation_strategy())
    flat = list(ability.generate_actions(policy.strategy, state, skip_unpayable_costs=True))
    expanded = list(expand(AbilityConstructionFrame(ability, policy), state))
    assert len(expanded) == len(flat) > 1
    assert set(map(ChoiceDescriptor.from_option, expanded)) == set(map(ChoiceDescriptor.from_option, flat))


def test_parameters_and_opaque_plan_pruning_use_their_own_contract():
    from game.game_actions.generation.decision_abstraction.parameters import AbilityParameters
    from game.game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy
    from game.game_actions.generation.generation_strategy import action_generation_strategy
    from game.game_actions.generation.pruning.pruning_strategy import LimitPruning
    from game.game_actions.generation.decision_abstraction.ability_frames import supports_construction
    game, request = fixture("lethal_shock")
    source = next(o for o in request.option_space(SearchGenerationPolicy(True)(request)) if type(o) is PrioritySourceOption)
    class Parameters:
        def generate(self, ability, state):
            return (AbilityParameters(0), AbilityParameters(2))
    policy = replace(source._policy, parameter_strategy=Parameters())
    options = list(AbilityConstructionFrame(source.ability, policy).options(game.state))
    assert [o.choice[0] for o in options] == [0, 2]
    assert not supports_construction(replace(policy, pruning=LimitPruning(1)))
    assert not supports_construction(AbilityGenerationPolicy(strategy=action_generation_strategy(
        action_plan_pruning=LimitPruning(1))))


def test_unpayable_cost_target_prefixes_are_never_offered():
    from helper_funcs import make_slot
    from test_ability_pipeline import node
    from game.game_actions.advanced_effects import SacrificeEffect
    from game.game_actions.data_structs.ability import ActivatedAbilityDefinition, SubAbilityDefinition
    from game.game_actions.generation.generation_strategy import action_generation_strategy
    from game.game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy
    game, request = fixture("useful_pacifism")
    targets = list(request.player.battlefield.values()) + list(game.state.players[1].battlefield.values())
    slot = make_slot("sacrifice", targets)
    effect = SacrificeEffect("pay")
    definition = ActivatedAbilityDefinition(cost_subdefs=(SubAbilityDefinition(action_node=node("pay", "sacrifice"),
        effects=frozenset({effect}), slots=frozenset({slot})),))
    ability = definition.to_ability(targets[0], request.player)
    frame = AbilityConstructionFrame(ability, AbilityGenerationPolicy(strategy=action_generation_strategy()))
    while frame.stage != "cost_targets":
        option, = frame.options(game.state)
        frame = option._next_frame
    offered = tuple(frame.options(game.state))
    assert len(offered) == 2
    assert all(option.choice[-1].owner is request.player for option in offered)
    assert len(tuple(expand(frame, game.state))) == 2


def test_construction_identity_never_reflects_policies_or_runtime_frames():
    from game.ai.search_actions import PlayerActionBuilder
    from game.ai.search_decision import DecisionProjector
    from game.ai.search_recall import PlayerRecall
    game, request = fixture("lethal_shock")
    source = next(o for o in request.option_space(SearchGenerationPolicy(True, True)(request))
                  if type(o) is PrioritySourceOption)
    frame = source._construction
    from game.game_actions.generation.decision_abstraction.parameters import AbilityParameters
    class Parameters:
        def generate(self, ability, state):
            return (AbilityParameters(0), AbilityParameters(1))
    frame = replace(frame, policy=replace(frame.policy, parameter_strategy=Parameters()))
    while True:
        options = tuple(frame.options(game.state))
        if len(options) > 1:
            break
        frame = options[0]._next_frame
    child = AbilityConstructionRequest(request.state, request.player, frame.ability, _frame=frame)
    information = DecisionProjector(child, options, PlayerRecall()).build().information
    encoded = PlayerActionBuilder(child).encode(options[0])
    assert encoded["stage"] == "parameters"
    encoded_again = PlayerActionBuilder(child).encode(replace(options[0], _next_frame=object()))
    assert encoded_again == encoded
    assert ChoiceDescriptor.from_option(options[0]) == ChoiceDescriptor.from_option(replace(options[0], _next_frame=object()))
    assert "_frame" not in information.current._json


def test_microchoice_budget_still_interrupts_without_paying_or_using_partial_results():
    from game.ai.search_simulator import SimulationBudgetExceeded
    simulator = SearchSimulator(tactical_scenario("lethal_shock"), policy_for=SearchGenerationPolicy(True, True),
                                track_information=True, max_decisions=1)
    root = simulator.start()
    source = next(a for a in root.player_information().actions if a.kind == "PrioritySourceOption")
    child = simulator.advance_action(root, source)
    with pytest.raises(SimulationBudgetExceeded):
        simulator.advance_action(child, child.player_information().actions[0])
    assert root.observation(0)["pending_abilities"] == []


def test_authored_resolver_keeps_legacy_signature_and_whole_group_rules():
    from helper_funcs import make_slot
    from dummy_classes import DummyEffect
    from test_ability_pipeline import setup_ability, node
    from game.target.target_resolver import TargetResolver
    from game.game_actions.generation.generation_strategy import action_generation_strategy
    from game.game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy
    from game.game_state import Card, CardDefinition
    from game.enums import CardType, ZoneType
    class AuthoredResolver(TargetResolver):
        # Deliberately has the original public signature and restricts groups
        # beyond the base resolver's individual-candidate predicate.
        def generate_target_options(self, source, controller, state, reserved=None):
            yield next(super().generate_target_options(source, controller, state, reserved))
    targets = []
    slot = make_slot("a", targets)
    slot = replace(slot, target_resolver=AuthoredResolver(slot.target_resolver.target_spec,
                                                        slot.target_resolver.target_selector))
    state, ability = setup_ability(node("damage", "a"), (DummyEffect("damage"),), (slot,))
    for name in ('one', 'two'):
        target = Card(CardDefinition(name, types=frozenset({CardType.CREATURE}), power=1, toughness=1), ability.controller)
        ability.controller.add_card(target, ZoneType.BATTLEFIELD)
        targets.append(target)
    policy = AbilityGenerationPolicy(strategy=action_generation_strategy())
    flat = tuple(ability.generate_actions(policy.strategy, state, skip_unpayable_costs=True))
    expanded = tuple(expand(AbilityConstructionFrame(ability, policy), state))
    assert len(flat) == len(expanded) == 1
    assert ChoiceDescriptor.from_option(flat[0]) == ChoiceDescriptor.from_option(expanded[0])
