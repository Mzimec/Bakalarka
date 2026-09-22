"""Composed generation preserves legality, budgets and agent/console agreement."""
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
import pytest
from game.enums import CardType, ZoneType, TurnPhase
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.ability import SubAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, OrActionNode, AndActionNode, ImmutableEffectToSlotMap
from game.game_actions.card_effects import TapSourceEffect
from game.game_actions.generation.generation_strategy import action_generation_strategy
from game.game_actions.generation.pruning.pruning_strategy import LimitPruning, FilterPruning
from game.game_actions.generation.decision_abstraction.parameters import AbilityParameters
from game.game_actions.generation.decision_abstraction.requests import AbilityDecisionRequest, PriorityDecisionRequest
from game.game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy, PriorityGenerationPolicy
from game.game_actions.generation.command_action_builder import ability_actions
from game.console.command_choices import legal_plans
from game.ai.modular_agent import CandidateGenerator
from game.game_actions.data_structs.game_action import PassPriorityAction, ConcedeAction


@pytest.fixture
def state():
    result = State([Player([], ScriptedController()), Player([], ScriptedController(), idx=1)])
    result.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return result


def bind(state, definition, zone=ZoneType.BATTLEFIELD):
    source = Card(CardDefinition("Source", types=frozenset({CardType.ARTIFACT}),
                                 abilities=frozenset({definition})), state.active_player)
    source.owner.add_card(source, zone, state)
    return definition.to_ability(source, source.owner)


def test_invalid_cost_does_not_consume_budget_and_console_agrees(state):
    tap = TapSourceEffect("tap")
    costs = OrActionNode((EffectActionNode(ImmutableEffectToSlotMap({"tap": frozenset()})), AndActionNode(())))
    definition = ActivatedAbilityDefinition(cost_subdefs=(SubAbilityDefinition(action_node=costs, effects=frozenset({tap})),))
    ability = bind(state, definition)
    ability.source.is_tapped = True
    policy = AbilityGenerationPolicy(strategy=action_generation_strategy(cost_plan_pruning=LimitPruning(1)))
    actions = list(AbilityDecisionRequest(state, state.active_player, ability).option_space(policy))
    assert len(actions) == 1
    assert actions[0].cost_generator.effects.sequence == ()
    console = list(legal_plans(ability, state, cost=True))
    assert len(console) == 1 and console[0][0] == 2
    assert console[0][1].effects == actions[0].cost_generator.effects
    # Explicitly requesting the invalid mode still reports a command error.
    with pytest.raises(ValueError):
        list(ability_actions(ability, state, cost_mode=1))
    assert ability.source.is_tapped and state.stack.is_empty()


def test_parameter_pruning_is_lazy_across_parameter_values(state):
    ability = bind(state, ActivatedAbilityDefinition())
    seen = []
    class Proposals:
        def generate(self, ability, state):
            for value in range(100):
                seen.append(value)
                yield AbilityParameters(value)
    policy = AbilityGenerationPolicy(parameter_strategy=Proposals(), pruning=LimitPruning(1))
    assert len(list(AbilityDecisionRequest(state, state.active_player, ability).option_space(policy))) == 1
    assert seen == [0]


def test_priority_collector_is_replaceable_and_pruning_precedes_expansion(state):
    ability = bind(state, ActivatedAbilityDefinition())
    calls = []
    class Source:
        def collect_lands(self, state, player):
            return iter(())
        def collect_abilities(self, state, player, *, include_mana):
            calls.append(include_mana)
            yield ability
    # The ability must not be expanded when filtered out, even with broken parameters.
    class Parameters:
        def generate(self, ability, state):
            raise AssertionError("Filtered abilities must not be expanded")
    class UnexpectedEquivalence:
        def bind(self, state):
            pytest.fail("Filtered abilities must not require an equivalence snapshot")
    policy = PriorityGenerationPolicy(
        collector=Source(), include_mana=False, equivalence=UnexpectedEquivalence(),
        ability_space_ps=FilterPruning(lambda ability: False),
        ability_gp=AbilityGenerationPolicy(parameter_strategy=Parameters()),
    )
    options = list(PriorityDecisionRequest(state, state.active_player).option_space(policy))
    assert [type(o) for o in options] == [PassPriorityAction, ConcedeAction]
    assert calls == [False]


def test_modular_agent_discovers_abilities_outside_old_hardcoded_zones(state):
    ability = bind(state, ActivatedAbilityDefinition(allowed_zones=frozenset({ZoneType.EXILE})), ZoneType.EXILE)
    candidates = CandidateGenerator().generate(state, state.active_player, set())
    key = (ability.source.command_id, ability.key)
    assert any(c.key == key for c in candidates)
    assert ability.source.get_zone() == ZoneType.EXILE
    assert all(c.key != key for c in CandidateGenerator().generate(state, state.active_player, {key}))


def test_effect_and_cost_budgets_apply_before_cartesian_product(state):
    # Independent modes make four combinations without any state mutation.
    first, second = TapSourceEffect("first"), TapSourceEffect("second")
    graph = OrActionNode(tuple(EffectActionNode(ImmutableEffectToSlotMap({effect.key: frozenset()}))
                               for effect in (first, second)))
    sub = SubAbilityDefinition(action_node=graph, effects=frozenset({first, second}))
    ability = bind(state, ActivatedAbilityDefinition(cost_subdefs=(sub,), action_subdefs=(sub,)))
    request = AbilityDecisionRequest(state, state.active_player, ability)
    assert len(list(request.options)) == 4
    bounded = action_generation_strategy(cost_plan_pruning=LimitPruning(1), action_plan_pruning=LimitPruning(1))
    assert len(list(request.option_space(AbilityGenerationPolicy(strategy=bounded)))) == 1
    assert not ability.source.is_tapped


def test_custom_solver_receives_reserved_cost_source_through_ability_pipeline(state):
    from game.mana.mana_solver import ManaSolver, PoolManaSolver
    from game.enums import ManaType
    tap = TapSourceEffect("tap")
    cost = SubAbilityDefinition(
        mana_cost="{R}", effects=frozenset({tap}),
        action_node=EffectActionNode(ImmutableEffectToSlotMap({"tap": frozenset()})),
    )
    ability = bind(state, ActivatedAbilityDefinition(cost_subdefs=(cost,)))
    state.active_player.mana_pool.add_pair(ManaType.RED, 1)
    reservations = []

    class RecordingSolver(ManaSolver):
        def get_mana_plan(self, mana_req, controller, state, *, reserved=frozenset()):
            reservations.append(reserved)
            return PoolManaSolver().get_mana_plan(mana_req, controller, state, reserved=reserved)

    policy = AbilityGenerationPolicy(strategy=action_generation_strategy(mana_solver=RecordingSolver()))
    actions = list(AbilityDecisionRequest(state, state.active_player, ability).option_space(policy))
    assert len(actions) == 1
    assert frozenset({ability.source}) in reservations
    assert actions[0].cost_generator.mana_solver_result.mana_plan == ()
    assert not ability.source.is_tapped
    assert state.active_player.mana_pool[ManaType.RED] == 1


def test_mana_solver_compatibility_imports_preserve_type_identity():
    from game.ai import mana_solver as old
    from game.mana import mana_solver as current
    for name in ("ManaSolver", "ManaSolverResult", "PoolManaSolver", "SourceActivatingManaSolver"):
        assert getattr(old, name) is getattr(current, name)
