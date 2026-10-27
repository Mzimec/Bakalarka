"""Prepared roots must reproduce reconstruction and isolate every branch."""
from random import Random

import pytest

from game.ai.current_position_worlds import _PreparedWorld, CurrentPositionWorlds
from game.ai.search_simulator import SearchSimulator
from game.ai.checkpoint_simulator import CheckpointSimulator
from game.ai.simulation_world import copy_world
from game.simulation.search_config import search_generation_policy, SearchAgentConfig
from test_current_position_worlds import position, prepare, controllers


@pytest.mark.parametrize("seed", [7, 31])
@pytest.mark.parametrize("checkpoints", [0, 4])
def test_prepared_root_matches_reconstruction_through_draws_and_siblings(seed, checkpoints):
    origin, live = position()
    _, _, snapshot = prepare(origin, live)
    from copy import copy
    reference_snapshot = copy(snapshot)
    reference_snapshot.immutable_values = {}
    reference_snapshot.dictionary_plans = None
    template = _PreparedWorld(snapshot, seed)
    options = dict(track_information=True, policy_for=search_generation_policy, max_phases=12)
    reference = SearchSimulator(lambda cs: reference_snapshot.build(cs, seed), **options)
    optimized = (CheckpointSimulator(template, checkpoint_limit=checkpoints, **options) if checkpoints
                 else SearchSimulator(template, **options))
    a, b = reference.start(), optimized.start()
    root = b
    rng = Random(9)
    live_rng = live.state.rng.getstate()
    for _ in range(35):
        assert a == b
        if a.decision is None:
            break
        assert a.player_information() == b.player_information()
        if len(a.history) < 4:
            for choice in a.choices[:2]:
                assert reference.advance(a, choice) == optimized.advance(b, choice)
        choice = rng.choice(a.choices)
        a, b = reference.advance(a, choice), optimized.advance(b, choice)
    assert a == b and optimized.fork(root) == root
    assert template.materializations == 1 and template.restores > 1
    assert live.state.rng.getstate() == live_rng


def test_template_does_not_share_cards_registry_rng_or_knowledge_with_branches():
    origin, live = position()
    _, _, snapshot = prepare(origin, live)
    template = _PreparedWorld(snapshot, 123)
    a = template.materialize(controllers()).state
    card = next(iter(a.players[0].hand.values()))
    a.players[0].health = 1
    a.players[0].controller.knowledge.events.append({"changed": True})
    a.rng.random()
    a.card_register.unregister(card)
    card.state.damage_marked = 99
    b = template.materialize(controllers()).state
    assert b.players[0].health != 1
    assert {"changed": True} not in b.players[0].controller.knowledge.events
    assert b.rng.getstate() == template.state.rng.getstate()
    other = b.card_register.get_by_key(card.key)
    assert other is not card and other.state.damage_marked != 99
    from game.game_state.registers.card_register import IK_OWNER
    assert b.card_register._snapshots[card.key][IK_OWNER] == frozenset({b.players[0]})
    for original in template.state.card_register.storage.get_all():
        cloned = b.card_register.get_by_key(original.key)
        assert cloned is not original and cloned._game_state is b
        assert cloned.runtime_id == original.runtime_id and cloned.command_id == original.command_id
        assert b.card_register._introductions[id(cloned)][0] is cloned
    assert all(p.controller is None for p in template.state.players)
    assert all(p.controller.knowledge._game_token is b.knowledge_token for p in b.players)


def test_templates_are_per_simulator_even_for_the_same_seed_and_config_can_disable():
    origin, live = position()
    _, info, snapshot = prepare(origin, live)
    from game.ai.decision_maker import PriorityDecisionRequest
    from game.ai.search_recall import PlayerRecall
    factory = CurrentPositionWorlds(origin, reuse_world=True, policy_for=search_generation_policy).prepare(
        PriorityDecisionRequest(live.state, live.state.players[0]), info,
        PlayerRecall(), live.state.players[0].controller.knowledge)
    first, second = factory(7), factory(7)
    assert first.factory is not second.factory
    assert first.start() == second.start()
    assert first.factory.state is not second.factory.state
    assert not SearchAgentConfig(deck_knowledge="both_lists").reuse_world
    assert not CurrentPositionWorlds(origin).reuse_world
    reference = _PreparedWorld(snapshot, 7, reuse=False)
    reference.materialize(controllers())
    reference.materialize(controllers())
    assert reference.materializations == 2 and reference.restores == 0 and reference.state is None
    for bad in (0, "false", None):
        with pytest.raises(ValueError, match="reuse_world"):
            CurrentPositionWorlds(origin, reuse_world=bad)
        with pytest.raises(ValueError, match="reuse_world"):
            SearchAgentConfig(deck_knowledge="both_lists", reuse_world=bad)


def test_world_restore_retains_consumed_static_replacement_budget():
    from game.game_state import Card, CardDefinition
    from game.enums import ZoneType
    from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition, active_effects
    origin, live = position()
    rule = ReplacementEffectDefinition("limited", lambda s, e, o: False, lambda s, e, o: (), uses=3, budget=5)
    card = Card(CardDefinition("Shield", replacement_effects=(rule,)), live.state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    effect = next(e for e in active_effects(live.state) if e.source is card)
    effect.remaining_uses, effect.remaining_budget = 1, 2
    copied, _ = copy_world(live.state, ())
    source = copied.card_register.get_by_key(card.key)
    other = next(e for e in active_effects(copied) if e.source is source)
    assert (other.remaining_uses, other.remaining_budget) == (1, 2)
    other.remaining_budget = 0
    assert effect.remaining_budget == 2


def test_shared_constants_exclude_runtime_references_and_custom_mutable_values():
    from dataclasses import FrozenInstanceError
    from game.ai.simulation_world import immutable_world_values
    from game.stat_type import STAT_POWER, STAT_CONTROLLER
    from game.game_state.stat import Stat
    from immutabledict import immutabledict
    origin, live = position()
    _, _, snapshot = prepare(origin, live)
    state = snapshot.materialize(controllers(), 7).state
    card = next(iter(state.players[0].hand.values()))
    constant = Stat(3, STAT_POWER)
    mutable = Stat([1], STAT_CONTROLLER)
    card._stats = immutabledict({**card.stats, STAT_POWER: constant, STAT_CONTROLLER: mutable})
    shared = immutable_world_values(state)
    assert shared[id(constant)] is constant
    assert id(mutable) not in shared and id(card.stats) not in shared
    assert all(id(p) not in shared for p in state.players)
    copied, _ = copy_world(state, (), immutable_values=shared)
    other = copied.card_register.get_by_key(card.key)
    other.stats[STAT_CONTROLLER].base_value.append(2)
    assert mutable.base_value == [1]
    with pytest.raises(FrozenInstanceError):
        other.stats[STAT_POWER].base_value = 4


def test_fast_set_copy_rebinds_members_and_preserves_cycles():
    from game.ai.runtime_clone import RuntimeClone
    class Member:
        pass
    item = Member()
    frozen = frozenset({item})
    item.members = frozen
    copied = RuntimeClone().copy((frozen, {item}))
    member = next(iter(copied[0]))
    assert member is not item and member.members is copied[0]
    assert next(iter(copied[1])) is member


def test_live_replacement_budget_survives_sampling_and_repeated_restore():
    from dataclasses import replace
    from game.cards.catalog import game_catalog
    from game.enums import ZoneType, TurnPhase
    from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition, active_effects
    rule = ReplacementEffectDefinition("limited", lambda s, e, o: False, lambda s, e, o: (), uses=3, budget=5)
    def catalog():
        result = game_catalog()
        result["Mountain"] = replace(result["Mountain"], replacement_effects=(rule,))
        return result
    origin, _ = position()
    origin = replace(origin, catalog_factory=catalog)
    live = origin.scenario(42)(controllers())
    card = next(c for c in live.state.players[0].get_cards() if c.name == "Mountain")
    card.owner.move_card(card, ZoneType.BATTLEFIELD)
    live.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    effect = next(e for e in active_effects(live.state) if e.source is card)
    effect.remaining_uses, effect.remaining_budget = 1, 2
    factory, info, snapshot = prepare(origin, live)
    for seed in (7, 31):
        sim = factory(seed)
        root = sim.start()
        assert root.player_information() == info and sim.fork(root) == root
        fresh = snapshot.materialize(controllers(), seed).state
        bound = next(e for e in active_effects(fresh) if e.source.key == card.key)
        assert (bound.remaining_uses, bound.remaining_budget) == (1, 2)


def test_bitset_copy_preserves_capacity_and_copies_custom_extra_state():
    from game.ai.runtime_clone import RuntimeClone
    from helper.query_system.object_register import BitSet
    original = BitSet((1 << 300) | 1)
    copied = RuntimeClone().copy(original)
    assert copied is not original and copied.__dict__ == original.__dict__
    copied._bits = 0
    assert original._bits != 0
    original.extra = [1]
    other = RuntimeClone().copy(original)
    other.extra.append(2)
    assert original.extra == [1]


def test_dictionary_recipes_keep_cycles_callbacks_runtime_keys_and_bounded_cache():
    from game.ai.runtime_clone import RuntimeClone, DictionaryCopyPlans
    class Member:
        pass
    member = Member()
    values = [1]
    def append(value):
        values.append(value)
    root = {"values": values, "alias": values, "append": append,
            "members": {member: values}, "constant": 17}
    root["self"] = root
    member.root = root
    plans = DictionaryCopyPlans()
    first = RuntimeClone(dictionary_plans=plans).copy(root)
    plans.collecting = False
    count = len(plans.plans)
    first["append"](2)
    assert first["values"] == [1, 2] and values == [1]
    for _ in range(3):
        copied = RuntimeClone(dictionary_plans=plans).copy(root)
        cloned_member = next(iter(copied["members"]))
        assert copied["self"] is copied and cloned_member.root is copied
        assert copied["alias"] is copied["values"] is copied["members"][cloned_member]
        assert copied["values"] == [1] and cloned_member is not member
        assert tuple(copied) == tuple(root)
        copied["append"](3)
        assert copied["values"] == [1, 3] and values == [1]
        # Temporary dictionaries produced by later reductions are not retained.
        assert len(plans.plans) == count
