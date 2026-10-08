"""Plan allocation shortcuts preserve live payment checks and mutable isolation."""
from types import SimpleNamespace

import pytest
from immutabledict import immutabledict

from game.enums import ManaType, SAVariableType
from game.game_state import State, Player
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.ability import RuntimeSubAbility
from game.game_actions.generation.exec_plan_gen_pipeline import ExecutionPlanPipeline
from game.game_actions.generation.execution_cost import EXECUTION_MANA_COST_COMPILER
from game.game_actions.generation.generation_strategy import execution_plan_strategy
from game.mana.mana_solver import PoolManaSolver
from game.mana.mana_value import ManaValue, ImmutableManaValue
from game.target.target_resolver import TargetBinding


@pytest.fixture
def setup():
    player = Player([], ScriptedController())
    state = State([player])
    ctx = SimpleNamespace(ability=SimpleNamespace(source=None, controller=player),
                          mana_cost=None, x_value=0, life_payment=None)
    return state, player, ctx


def plans(state, ctx, *, cost=None, solver=True):
    subability = RuntimeSubAbility(None, mana_cost=cost) if cost is not None else None
    strategy = execution_plan_strategy(mana_solver=PoolManaSolver() if solver else None)
    return list(ExecutionPlanPipeline().generate(ctx, subability, strategy, state))


def test_combined_spell_and_additional_cost_rechecks_the_live_pool(setup):
    state, player, ctx = setup
    ctx.mana_cost = ImmutableManaValue("{W}")
    extra = ImmutableManaValue("{U}")
    player.mana_pool.add({ManaType.WHITE: 1})
    assert plans(state, ctx, cost=extra) == []
    player.mana_pool.add({ManaType.BLUE: 1})
    plan, = plans(state, ctx, cost=extra)
    assert plan.mana_solver_result.payment == {ManaType.WHITE: 1, ManaType.BLUE: 1}
    assert player.mana_pool == {ManaType.WHITE: 1, ManaType.BLUE: 1}
    player.mana_pool.clear()
    assert plans(state, ctx, cost=extra) == []


def test_phyrexian_life_choice_and_health_are_checked_for_each_plan(setup):
    state, player, ctx = setup
    ctx.mana_cost = ImmutableManaValue("{U/P}")
    ctx.life_payment = 2
    plan, = plans(state, ctx)
    assert plan.mana_solver_result.life_payment == 2
    player.health = 1
    assert plans(state, ctx) == []
    player.health = 20
    ctx.life_payment = 0
    assert plans(state, ctx) == []
    player.mana_pool.add({ManaType.BLUE: 1})
    plan, = plans(state, ctx)
    assert plan.mana_solver_result.life_payment == 0


def test_variable_cost_and_parameters_follow_each_request(setup):
    state, player, ctx = setup
    ctx.mana_cost = ImmutableManaValue("{X}{U}")
    player.mana_pool.add({ManaType.BLUE: 2})
    ctx.x_value = 1
    first, = plans(state, ctx)
    assert first.mana_solver_result.payment == {ManaType.BLUE: 2}
    ctx.x_value = 2
    assert plans(state, ctx) == []
    assert first.param_context.x_variables[SAVariableType.X] == 1


def test_empty_plans_keep_independent_mutable_metadata_and_validate(setup, monkeypatch):
    state, player, ctx = setup
    seen = []
    original = TargetBinding.are_targets_valid

    def validate(binding, source, controller, state, slots):
        seen.append(tuple(slots))
        return original(binding, source, controller, state, slots)

    monkeypatch.setattr(TargetBinding, "are_targets_valid", validate)
    first, = plans(state, ctx)
    second, = plans(state, ctx)
    assert seen == [(), ()]
    first.binding.zone_revisions[123] = 9
    first.binding.resolution_filtered = True
    first.param_context.x_variables = immutabledict({SAVariableType.X: 7})
    assert not second.binding.zone_revisions and not second.binding.resolution_filtered
    assert second.param_context.x_variables[SAVariableType.X] == 0
    ctx.life_payment = 2
    assert plans(state, ctx) == []


@pytest.mark.parametrize("x", [-1, False, 0.5])
def test_empty_cost_still_rejects_invalid_x(setup, x):
    state, _, ctx = setup
    ctx.x_value = x
    with pytest.raises(ValueError, match="X must be"):
        plans(state, ctx)


def test_effect_plan_ignores_casting_cost_but_rejects_its_own_unpaid_cost(setup):
    state, _, ctx = setup
    ctx.mana_cost = ImmutableManaValue("{7}")
    plan, = plans(state, ctx, solver=False)
    assert plan.mana_solver_result is None
    with pytest.raises(ValueError, match="mana solver is required"):
        plans(state, ctx, cost=ImmutableManaValue("{1}"), solver=False)


def test_mutable_cost_is_snapshotted_and_custom_cost_is_normalized():
    mutable = ManaValue("{U}")
    compiled = EXECUTION_MANA_COST_COMPILER.combine(None, mutable)
    mutable.add(ManaValue("{W}"))
    assert compiled == ImmutableManaValue("{U}")

    class CustomCost(ImmutableManaValue):
        def payment_options(self, x_value=0):
            pytest.fail("Custom input methods must not replace normalized cost expansion")

    compiled = EXECUTION_MANA_COST_COMPILER.combine(CustomCost("{W}"), "{U}")
    assert compiled == ImmutableManaValue("{W}{U}")
    assert len(list(compiled.payment_options())) == 1
