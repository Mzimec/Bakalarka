"""Failed payments restore runtime identities, event visibility and effect budgets."""
from dataclasses import dataclass
import pytest

from game.console.demo_game import create_demo_game
from game.game_loop.minimal_game import ScriptedController
from game.game_state import Card, CardDefinition
from game.enums import CardType, CounterType, ZoneType, TurnPhase, ManaType
from game.game_actions.data_structs.operation import Operation
from game.game_actions.data_structs.game_action import (
    AbilityAction, FixedExecutionPlan, ResolutionContext, ScheduledResolution, GameAction,
)
from game.game_actions.mana_effects import PayLifeOperation, SpendManaOperation
from game.operations.card_operations import MoveCardOperation, TapCardOperation
from game.game_actions.resolution.event_bus import GameEvent, EventBus
from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition
from helper.query_system.query import EqQuery
from game.game_state.registers.card_register import IK_ZONE


@pytest.fixture
def game():
    g = create_demo_game((ScriptedController(), ScriptedController()), full_rules=True)
    g.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return g


def source(game, key="source"):
    player = game.state.active_player
    card = Card(CardDefinition("Source", types=frozenset({CardType.CREATURE}), power=2,
                               toughness=2, keywords=frozenset({"haste"})), player, key)
    player.add_card(card, ZoneType.BATTLEFIELD)
    return card


def action(card, operations):
    return AbilityAction("pay", card, FixedExecutionPlan([]), FixedExecutionPlan(operations), controller=card.owner)


class UnknownCost(Operation):
    def execute(self, state):
        self.context.controller.health -= 1
        return []


def test_unknown_cost_with_only_validation_error_is_rejected_before_any_payment(game):
    card = source(game)
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    unknown = UnknownCost(ctx)
    unknown.validation_error = lambda state: None
    health = card.owner.health
    result = game.loop.processor.process(game.state, action(card, (PayLifeOperation(ctx, 1), unknown)))
    assert not result[0].success and "reserve_cost" in result[0].error.message
    assert card.owner.health == health and game.state.stack.is_empty()


class SpendEnergy(Operation):
    def __init__(self, context, amount=1, fail=False):
        super().__init__(context)
        self.amount, self.fail = amount, fail

    def reserve_cost(self, state, resources):
        return resources.reserve((self.context.controller, "energy"), self.amount, self.context.controller.energy)

    def execute(self, state):
        self.context.controller.energy -= self.amount
        if self.fail:
            raise RuntimeError("broken custom operation")
        return [GameEvent("energy_paid", self.context.source, self.context.controller)]


def test_custom_costs_reserve_the_same_resource_together(game):
    card = source(game)
    card.owner.energy = 1
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    result = game.loop.processor.process(game.state, action(card, (SpendEnergy(ctx), SpendEnergy(ctx))))
    assert not result[0].success and card.owner.energy == 1
    result = game.loop.processor.process(game.state, action(card, (SpendEnergy(ctx),)))
    assert result[0].success and card.owner.energy == 0


def test_exception_after_partial_custom_mutation_restores_state_then_propagates(game):
    card = source(game)
    card.owner.energy = 1
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    with pytest.raises(RuntimeError, match="broken custom"):
        game.loop.processor.process(game.state, action(card, (TapCardOperation(ctx, card), SpendEnergy(ctx, fail=True))))
    assert card.owner.energy == 1 and not card.is_tapped
    assert game.state.stack.is_empty()
    assert game.loop.processor.executor._pending_triggers is None
    assert game.loop.processor.executor._cost_transaction is None


def test_replacement_failure_restores_zone_incarnation_attachments_combat_and_budget(game):
    card = source(game)
    aura = source(game, "attached")
    aura.attach(card, game.state)
    card.state.counters[CounterType.PLUS_ONE] = 2
    game.state.combat.attackers[card] = game.state.players[1]
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    card.owner.mana_pool.add({ManaType.RED: 1})
    def transform(state, effect, operation):
        effect.remaining_budget -= 1
        return (PayLifeOperation(ctx, card.owner.health + 1),)
    replacement = ReplacementEffectDefinition("fail", lambda s, e, op: isinstance(op, SpendManaOperation),
                                               transform, uses=1, budget=2).bind()
    game.state.replacement_rules.append(replacement)
    before_revision, before_id, before_stats = card.zone_revision, card.runtime_id, card.stats
    zone_object, counters_object = card.owner.battlefield, card.state.counters
    bus = game.loop.processor.executor._event_bus
    before_events = tuple(bus.emitted_events)
    costs = (MoveCardOperation(ctx, card, ZoneType.GRAVEYARD), SpendManaOperation(ctx, {ManaType.RED: 1}))
    result = game.loop.processor.process(game.state, action(card, costs))
    assert not result[0].success
    assert card.get_zone() == ZoneType.BATTLEFIELD and card.zone_revision == before_revision
    assert card.runtime_id == before_id and card.stats is before_stats
    assert card.owner.battlefield is zone_object and card.state.counters is counters_object
    assert card.state.counters[CounterType.PLUS_ONE] == 2
    assert aura.attached_to is card and card.state.attached[aura.key] is aura
    assert card in game.state.combat.attackers
    assert replacement.remaining_uses == 1 and replacement.remaining_budget == 2
    assert dict(card.owner.mana_pool) == {ManaType.RED: 1}
    assert tuple(bus.emitted_events) == before_events and game.state.stack.is_empty()
    assert card in game.state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD))
    assert card not in game.state.query_cards(EqQuery(IK_ZONE, ZoneType.GRAVEYARD))


@dataclass(frozen=True)
class SeveralCosts(GameAction):
    resolutions: tuple

    def get_intents(self):
        return self.resolutions


def test_failure_in_later_cost_intent_rolls_back_earlier_intent_and_stack_routing(game):
    card = source(game)
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    before = card.owner.health
    command = SeveralCosts((ScheduledResolution(FixedExecutionPlan([PayLifeOperation(ctx, 1)]), ctx),
        ScheduledResolution(FixedExecutionPlan([]), ResolutionContext(uses_stack=True)),
        ScheduledResolution(FixedExecutionPlan([PayLifeOperation(ctx, before)]), ctx)))
    result = game.loop.processor.process(game.state, command)
    assert len(result) == 1 and not result[0].success
    assert card.owner.health == before and game.state.stack.is_empty()


def test_event_bus_does_not_observe_tentative_costs(game):
    card = source(game)
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    published = []
    class ObservedBus(EventBus):
        def emit(self, event, state=None):
            published.append(event.key)
            return super().emit(event, state)
    game.loop.processor.executor._event_bus = ObservedBus()
    result = game.loop.processor.process(game.state, action(card, (PayLifeOperation(ctx, 1), UnknownCost(ctx))))
    assert not result[0].success and published == []
    assert game.loop.processor.process(game.state, action(card, (PayLifeOperation(ctx, 1),)))[0].success
    assert published == ["life_paid"]


def test_replaced_zone_change_still_pays_cost_and_is_not_reapplied(game):
    from game.game_actions.resolution.replacement_effects import replace_zone
    card = source(game)
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    rule = replace_zone("exile", ZoneType.BATTLEFIELD, ZoneType.GRAVEYARD, ZoneType.EXILE, uses=1).bind()
    game.state.replacement_rules.append(rule)
    result = game.loop.processor.process(game.state, action(card, (MoveCardOperation(ctx, card, ZoneType.GRAVEYARD),)))
    assert result[0].success and card.zone == ZoneType.EXILE and rule.remaining_uses == 0
    assert len(game.state.stack.items) == 1


def test_direct_cost_resolution_uses_transaction_too(game):
    card = source(game)
    card.owner.energy = 1
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    scheduled = ScheduledResolution(FixedExecutionPlan([PayLifeOperation(ctx, 1), SpendEnergy(ctx, fail=True)]), ctx)
    before = card.owner.health
    with pytest.raises(RuntimeError):
        game.loop.processor.executor.resolve(game.state, scheduled)
    assert card.owner.health == before and card.owner.energy == 1


def test_replacement_moving_a_later_cost_object_invalidates_payment(game):
    first, second = source(game, "first"), source(game, "second")
    ctx = ResolutionContext(source=first, controller=first.owner, is_cost=True)
    rule = ReplacementEffectDefinition("move-later-cost", lambda s, e, op: isinstance(op, PayLifeOperation),
        lambda s, e, op: (MoveCardOperation(ctx, second, ZoneType.EXILE),)).bind()
    game.state.replacement_rules.append(rule)
    result = game.loop.processor.process(game.state, action(first, (PayLifeOperation(ctx, 1), TapCardOperation(ctx, second))))
    assert not result[0].success and "changed zones" in result[0].error.message
    assert second.get_zone() == ZoneType.BATTLEFIELD and not second.is_tapped


def test_new_custom_cost_entity_is_detached_on_rollback(game):
    card = source(game)
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    created = Card(card.definition, card.owner, "created")
    class CreateThenFail(Operation):
        def reserve_cost(self, state, resources):
            return None

        def execute(self, state):
            card.owner.add_card(created, ZoneType.BATTLEFIELD)
            raise RuntimeError("created then failed")
    with pytest.raises(RuntimeError):
        game.loop.processor.process(game.state, action(card, (CreateThenFail(ctx),)))
    assert card.owner.try_find_card(created.key) is None
    assert created._game_state is None and created.runtime_id is None


def test_explicit_tap_creature_cost_does_not_use_tap_symbol_sickness_rule(game):
    player = game.state.active_player
    card = Card(CardDefinition("Fresh", types=frozenset({CardType.CREATURE}), power=1, toughness=1), player, "fresh")
    player.add_card(card, ZoneType.BATTLEFIELD)
    ctx = ResolutionContext(source=card, controller=player, is_cost=True)
    assert card.is_summoning_sick(game.state)
    assert not game.loop.processor.process(game.state, action(card, (TapCardOperation(ctx, card),)))[0].success
    assert game.loop.processor.process(game.state, action(card, (TapCardOperation(ctx, card, tap_symbol=False),)))[0].success
