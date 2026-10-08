"""Scoped costs retain the full transaction's externally visible rollback."""
from dataclasses import dataclass

import pytest

from game.enums import CardType, ManaType, TurnPhase, ZoneType
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.game_action import (
    GameAction, AbilityAction, FixedExecutionPlan, ScheduledResolution, ResolutionContext,
)
from game.game_actions.data_structs.ability import TriggerAbilityDefinition, TriggeredManaAbilityDefinition
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus, GameEvent
from game.game_actions.resolution.cost_transaction import CostTransaction, RuntimeCheckpoint
from game.game_actions.resolution.cost_scope import card_write_scope
from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition
from game.game_actions.mana_effects import PayLifeOperation, AddManaOperation, SpendManaOperation
from game.game_actions.life_effects import GainLifeOperation
from game.operations.card_operations import TapCardOperation, MoveCardOperation


@pytest.fixture
def setup():
    player = Player([], ScriptedController())
    state = State([player, Player([], ScriptedController())])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    cards = []
    for key, zone in (("land", ZoneType.BATTLEFIELD), ("spell", ZoneType.HAND),
                      ("unrelated", ZoneType.DECK)):
        card = Card(CardDefinition(key, types=frozenset({CardType.LAND})), player, key)
        player.add_card(card, zone)
        cards.append(card)
    engine = ResolutionEngine(OperationExecutor(), EventBus(), sba_rules=[])
    state.synchronise_registers()
    return state, ActionProcessor(engine), cards


@dataclass(frozen=True)
class MultipleCosts(GameAction):
    intents: tuple

    def get_intents(self):
        return self.intents


def intent(context, *operations):
    return ScheduledResolution(FixedExecutionPlan(operations), context)


def assert_restored(checkpoint):
    for kind, obj, saved in checkpoint._restore:
        if kind == "object":
            assert vars(obj) == saved, type(obj)
        elif kind == "dict":
            assert dict(obj) == saved
        elif kind in ("list", "set"):
            assert obj == saved
        elif kind == "deque":
            assert tuple(obj) == saved
        else:
            assert obj.getstate() == saved


def test_scoped_payment_restores_all_full_checkpoint_values_after_later_failure(setup):
    state, processor, (land, spell, unrelated) = setup
    context = ResolutionContext(source=spell, controller=spell.owner, is_cost=True)
    paid = intent(context, TapCardOperation(context, land), AddManaOperation(context, ManaType.WHITE, 1),
                  SpendManaOperation(context, {ManaType.WHITE: 1}), MoveCardOperation(context, spell, ZoneType.STACK))
    routed = intent(ResolutionContext(uses_stack=True))
    failure = intent(context, SpendManaOperation(context, {ManaType.WHITE: 1}))
    action = MultipleCosts((paid, routed, failure))
    scope = card_write_scope(state, action.intents)
    assert scope == {land, spell}
    scoped = RuntimeCheckpoint(state, card_scope=scope)
    assert unrelated not in [obj for kind, obj, _ in scoped._restore if kind == "object"]
    original_row = state.card_register._snapshots[spell.key]
    assert not any(obj is original_row for _, obj, _ in scoped._restore)
    before = RuntimeCheckpoint(state)
    assert not processor.process(state, action)[0].success
    assert_restored(before)
    assert state.stack.is_empty() and not land.is_tapped and spell.zone == ZoneType.HAND
    assert state.card_register._snapshots[spell.key] is original_row


def test_direct_cost_and_operation_owned_fields_are_restored(setup):
    state, processor, (land, spell, _) = setup
    context = ResolutionContext(source=spell, controller=spell.owner, is_cost=True)
    move = MoveCardOperation(context, spell, ZoneType.STACK)
    resolution = intent(context, TapCardOperation(context, land), move)
    transaction = CostTransaction(state, resolutions=(resolution,))
    assert transaction.checkpoint.mode == "scoped"
    transaction.watch_operations((move,))
    move.entry_counters["test"] = 2
    land.is_tapped = True
    transaction.rollback()
    assert move.entry_counters == {} and not land.is_tapped
    assert processor.executor.resolve(state, resolution).success
    assert land.is_tapped and spell.zone == ZoneType.STACK


def test_custom_replacement_keeps_full_rollback_of_unrelated_card(setup):
    state, processor, (land, _, unrelated) = setup
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True)

    def transform(state, effect, operation):
        unrelated.state.damage_marked = 7
        return (PayLifeOperation(context, 1000),)

    state.replacement_rules.append(ReplacementEffectDefinition(
        "custom", lambda s, e, op: True, transform).bind())
    resolution = intent(context, PayLifeOperation(context, 1))
    assert card_write_scope(state, (resolution,)) is None
    assert not processor.executor.resolve(state, resolution).success
    assert unrelated.state.damage_marked == 0


def test_irrelevant_replacement_types_are_enforced_before_callbacks(setup):
    state, processor, (land, _, _) = setup
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True)

    def forbidden(*args):
        raise AssertionError("A mana payment must not invoke a life-gain replacement.")

    state.replacement_rules.append(ReplacementEffectDefinition(
        "gain", forbidden, forbidden, operation_types=(GainLifeOperation,)).bind())
    resolution = intent(context, PayLifeOperation(context, 1))
    assert card_write_scope(state, (resolution,)) is not None
    assert processor.executor.resolve(state, resolution).success
    assert land.owner.health == 19


@pytest.mark.parametrize("zone", [ZoneType.BATTLEFIELD, ZoneType.HAND])
def test_mana_triggers_in_any_active_zone_use_full_checkpoint(setup, zone):
    state, _, (land, _, _) = setup
    definition = TriggeredManaAbilityDefinition(allowed_zones=frozenset({zone}))
    card = Card(CardDefinition("trigger", triggers=(definition,)), land.owner, "trigger")
    land.owner.add_card(card, zone)
    state.synchronise_registers()
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True)
    assert card_write_scope(state, (intent(context, PayLifeOperation(context, 1)),)) is None


def test_empty_trigger_cost_only_records_stack_and_registration(setup):
    state, _, (land, _, _) = setup
    from game.game_actions.triggers.runtime_triggers import register_trigger
    definition = TriggerAbilityDefinition()
    context = ResolutionContext(source=land, controller=land.owner)
    registration = register_trigger(state, context, definition, predicate=lambda s: True)
    action = AbilityAction("trigger", land, FixedExecutionPlan([]), FixedExecutionPlan([]),
                           ability=definition, trigger_event=GameEvent("test"),
                           trigger_registration=registration)
    transaction = CostTransaction(state, action=action, resolutions=action.get_intents())
    assert transaction.checkpoint.mode == "stack"
    before = RuntimeCheckpoint(state)
    state.stack.push(action.get_intents()[1].to_stack_item())
    registration.pending = registration.queued = True
    transaction.rollback()
    assert_restored(before)


def test_scope_analysis_does_not_consume_custom_iterables(setup):
    state, _, (land, _, _) = setup
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True)
    operations = iter((PayLifeOperation(context, 1),))
    resolution = ScheduledResolution(FixedExecutionPlan(operations), context)
    assert card_write_scope(state, (resolution,)) is None
    assert len(tuple(operations)) == 1


def test_relevant_loyalty_replacement_cannot_use_card_scope(setup):
    state, _, (land, _, _) = setup
    from game.rules.permanents import LoyaltyCostOperation
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True)
    state.replacement_rules.append(ReplacementEffectDefinition(
        "loyalty", lambda *args: True, lambda *args: (),
        operation_types=(LoyaltyCostOperation,)).bind())
    assert card_write_scope(state, (intent(context, PayLifeOperation(context, 1)),)) is None


def test_continuous_memberships_and_indexes_restore_across_life_threshold(setup):
    state, processor, (land, _, _) = setup
    from game.cards.starter_cards import ANGEL_OF_VITALITY
    angel = Card(ANGEL_OF_VITALITY, land.owner, "angel")
    land.owner.add_card(angel, ZoneType.BATTLEFIELD)
    land.owner.health = 25
    state.synchronise_registers()
    assert angel.get_power(state) == 4
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True)
    action = MultipleCosts((intent(context, PayLifeOperation(context, 1)),
                            intent(context, PayLifeOperation(context, 25))))
    assert card_write_scope(state, action.intents) is not None
    before = RuntimeCheckpoint(state)
    assert not processor.process(state, action)[0].success
    assert_restored(before)
    assert angel.get_power(state) == 4 and land.owner.health == 25


def test_empty_generator_does_not_imply_empty_cost_with_loyalty(setup):
    state, _, (land, _, _) = setup
    from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
    context = ResolutionContext(source=land, controller=land.owner, is_cost=True,
                                ability=ActivatedAbilityDefinition(loyalty_cost=1))
    transaction = CostTransaction(state, resolutions=(intent(context),))
    assert transaction.checkpoint.mode == "scoped"
    assert land in transaction.checkpoint.card_scope


@pytest.mark.parametrize("restriction", [[GainLifeOperation], (int,)])
def test_replacement_type_restriction_rejects_invalid_metadata(restriction):
    with pytest.raises(ValueError, match="Operation classes"):
        ReplacementEffectDefinition("bad", lambda *args: True, lambda *args: (),
                                    operation_types=restriction)


@pytest.mark.parametrize("equip", [False, True])
def test_attachment_payment_rolls_back_before_attachment_resolves(setup, equip):
    from game.enums import CardSubtype
    from game.game_actions.permanent_effects import attachment_ability
    from game.console.demo_game import build_action
    state, processor, (land, _, _) = setup
    host = Card(CardDefinition("host", types=frozenset({CardType.CREATURE}),
                               power=2, toughness=2), land.owner, "host")
    land.owner.add_card(host, ZoneType.BATTLEFIELD)
    item = Card(CardDefinition("item", types=frozenset({CardType.ARTIFACT if equip else CardType.ENCHANTMENT}),
                               subtypes=frozenset({CardSubtype.EQUIPMENT if equip else CardSubtype.AURA}),
                               abilities=frozenset({attachment_ability(equip=equip)})), land.owner, "item")
    land.owner.add_card(item, ZoneType.BATTLEFIELD if equip else ZoneType.HAND)
    action = build_action(state, land.owner, "activate item equip host" if equip else "play item host")
    state.synchronise_registers()
    resolutions = action.get_intents()
    scope = card_write_scope(state, resolutions)
    assert scope is not None and item in scope
    failure = intent(ResolutionContext(source=item, controller=item.owner, is_cost=True),
                     SpendManaOperation(ResolutionContext(controller=item.owner), {ManaType.WHITE: 100}))
    before = RuntimeCheckpoint(state)
    result = processor.process(state, MultipleCosts((*resolutions, failure)))
    assert not result[0].success
    assert_restored(before)
    assert item.attached_to is None and not host.state.attached


def test_custom_attachment_validator_retains_full_checkpoint(setup):
    from game.game_actions.permanent_effects import AuraCastAbilityDefinition
    state, _, (_, spell, _) = setup
    definition = AuraCastAbilityDefinition()
    object.__setattr__(definition, "validation_error", lambda *args: None)
    context = ResolutionContext(source=spell, controller=spell.owner, is_cost=True, ability=definition)
    assert card_write_scope(state, (intent(context),)) is None


def test_scope_fallback_reason_is_counted_only_by_profiler(setup):
    from benchmarks.profiler import ComponentProfiler
    from benchmarks.probes import install_cost_checkpoint_probe
    from game.game_actions.resolution import cost_scope
    state, _, (_, spell, _) = setup
    context = ResolutionContext(source=spell, controller=spell.owner, is_cost=True)
    original = cost_scope._full_checkpoint
    profiler = ComponentProfiler()
    install_cost_checkpoint_probe(profiler)
    try:
        assert card_write_scope(state, (intent(context, GainLifeOperation(context, 1)),)) is None
        assert profiler.work_to_dict()["cost.scope.fallback.unsupported_operation"] == 1
    finally:
        profiler.restore()
    assert cost_scope._full_checkpoint is original
