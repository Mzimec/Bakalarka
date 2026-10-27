"""Checkpoint branches must reproduce strict engine replay, including memory."""
from random import Random

import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.checkpoint_simulator import CheckpointSimulator, _copy_world
from game.ai.search_simulator import SearchSimulator


@pytest.mark.parametrize("case", ["lethal_shock", "answered_shock", "attack_pacified", "redundant_pacifism"])
def test_priority_checkpoints_match_strict_replay_across_branches(case):
    options = dict(policy_for=tactical_policy, track_information=True, max_phases=8)
    strict = SearchSimulator(tactical_scenario(case), **options)
    fast = CheckpointSimulator(tactical_scenario(case), checkpoint_limit=8, checkpoint_interval=1, **options)
    a, b = strict.start(), fast.start()
    assert a == b
    root = b
    rng = Random(7)
    for _ in range(24):
        if a.decision is None:
            break
        # Exercise sibling branches too, rather than only a single cached path.
        for choice in a.choices[:2]:
            assert strict.advance(a, choice) == fast.advance(b, choice)
        choice = rng.choice(a.choices)
        a, b = strict.advance(a, choice), fast.advance(b, choice)
        assert a == b
        assert fast.fork(b) == b
    assert fast.fork(root) == root
    assert fast.checkpoint_hits > 0
    assert 0 < len(fast._checkpoints) <= 8


def test_world_copy_preserves_rng_and_registry_identity_without_sharing():
    from game.ai.modular_agent import ModularAgent
    from game.ai.knowledge import PlayerKnowledge
    game = tactical_scenario("attack_pacified")((ModularAgent(), ModularAgent()))
    state = game.state
    state.rng = Random(19)
    memories = (PlayerKnowledge(), PlayerKnowledge())
    for i, memory in enumerate(memories):
        memory.bind(state.knowledge_token, i)
    clone, copied_memories = _copy_world(state, memories)
    assert clone.rng is not state.rng
    assert clone.rng.random() == Random(19).random()
    assert state.rng.getstate() == Random(19).getstate()
    for memory in copied_memories:
        assert memory._game_token is clone.knowledge_token
    for card in state.card_register.storage.get_all():
        other = clone.card_register.get_by_key(card.key)
        assert other is not card
        assert other._game_state is clone
        assert other.command_id == card.command_id and other.runtime_id == card.runtime_id
        assert clone.card_register._introductions[id(other)][0] is other
        assert clone.card_register._assigned_command_ids[id(other)][1] == card.command_id
    clone.players[0].health -= 3
    assert clone.players[0].health != state.players[0].health


def test_zero_limit_is_strict_replay_and_invalid_limits_are_rejected():
    fast = CheckpointSimulator(tactical_scenario("lethal_shock"), checkpoint_limit=0,
                               policy_for=tactical_policy, track_information=True)
    node = fast.start()
    assert fast.fork(node) == node
    assert not fast._checkpoints and fast.checkpoint_hits == 0
    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError):
            CheckpointSimulator(tactical_scenario("lethal_shock"), checkpoint_limit=invalid)


@pytest.mark.parametrize("seed", [7, 31])
def test_current_root_checkpoint_keeps_sampled_draws_memories_and_phase_budget(seed):
    from test_current_position_worlds import position, prepare
    from game.ai.search_recall import PlayerRecall
    origin, game = position()
    prior = PlayerRecall().append("event", {"kind": "earlier_public_evidence"})
    _, _, snapshot = prepare(origin, game, recall=prior)
    def factory(controllers):
        return snapshot.build(controllers, seed)
    options = dict(policy_for=tactical_policy, track_information=True, max_phases=20,
                   initial_recalls=((snapshot.observer, prior),))
    strict = SearchSimulator(factory, **options)
    fast = CheckpointSimulator(factory, checkpoint_limit=4, **options)
    a, b = strict.start(), fast.start()
    before = game.state.rng.getstate()
    for _ in range(60):
        assert a == b
        if a.decision is None:
            break
        choice = next((c for c in a.choices if c.kind == "PassPriorityAction"), a.choices[0])
        a, b = strict.advance(a, choice), fast.advance(b, choice)
    assert a == b and fast.checkpoint_skipped_decisions > 0
    assert a.cutoff or a.terminal
    assert game.state.rng.getstate() == before


def test_cloned_mutable_mapping_view_tracks_changes_and_is_branch_local():
    from game.ai.runtime_clone import RuntimeClone
    from game.mana.mana_value import ManaPool
    from game.enums import ManaType
    from helper.dict_helper import MutableSetMapping
    pool = ManaPool({ManaType.RED: 1})
    copied = RuntimeClone().copy(pool)
    copied.add({ManaType.RED: 2})
    assert dict(copied) == {ManaType.RED: 3}
    assert dict(pool) == {ManaType.RED: 1}
    copied.clear()
    assert dict(copied) == {} and pool[ManaType.RED] == 1
    original = MutableSetMapping({"x": {1}})
    other = RuntimeClone().copy(original)
    other.mutate_pair("y", {2}, set.update)
    assert dict(other) == {"x": {1}, "y": {2}} and dict(original) == {"x": {1}}


def test_follow_keeps_running_world_mutable_until_actual_suspension():
    options = dict(policy_for=tactical_policy, track_information=True)
    strict = SearchSimulator(tactical_scenario("attack_pacified"), **options)
    fast = CheckpointSimulator(tactical_scenario("attack_pacified"), checkpoint_interval=1, **options)
    def follow(node):
        if len(node.history) >= 3 and node.decision.kind == "PriorityDecisionRequest":
            return None
        return next((a for a in node.player_information().actions if a.kind == "PassPriorityAction"),
                    node.player_information().actions[0])
    a, b = strict.follow_actions(follow), fast.follow_actions(follow)
    assert a == b and fast.fork(b) == b
    assert len(fast._checkpoints) == 1
    for choice in a.choices[:2]:
        assert strict.advance(a, choice) == fast.advance(b, choice)


def test_priority_request_without_native_continuation_marker_is_not_captured():
    from game.ai.decision_maker import PriorityDecisionRequest
    def factory(controllers):
        game = tactical_scenario("lethal_shock")(controllers)
        for _ in range(6):
            controllers[0].decide(PriorityDecisionRequest(game.state, game.state.players[0]))
        return game
    sim = CheckpointSimulator(factory, checkpoint_interval=1, track_information=True,
                              policy_for=tactical_policy)
    def follow(node):
        if len(node.history) == 4:
            return None
        return next(a for a in node.player_information().actions if a.kind == "PassPriorityAction")
    node = sim.follow_actions(follow)
    assert len(node.history) == 4 and not sim._checkpoints
    assert sim.fork(node) == node


@pytest.mark.parametrize("reuse", [False, True])
def test_checkpoint_reuses_only_proven_root_constants_and_preserves_ownership(reuse):
    from game.game_state.registers.card_register import IK_OWNER
    from game.ai.current_position_worlds import _PreparedWorld
    from test_current_position_worlds import position, prepare
    origin, game = position()
    _, _, snapshot = prepare(origin, game)
    template = _PreparedWorld(snapshot, 7, reuse=reuse)
    sim = CheckpointSimulator(template, checkpoint_interval=1,
                              track_information=True, policy_for=tactical_policy)
    def stop_at_priority(node):
        if len(node.history) > 0 and node.decision.kind == "PriorityDecisionRequest":
            return None
        return next((a for a in node.player_information().actions if a.kind == "PassPriorityAction"),
                    node.player_information().actions[0])
    sim.follow_actions(stop_at_priority)
    checkpoint = next(iter(sim._checkpoints.values()))
    constants = checkpoint._immutable_values
    if reuse:
        assert constants is template.immutable_values
    else:
        assert not constants
    original_size = len(constants)
    original_life = checkpoint.state.players[0].health
    a, memories_a = checkpoint.copy()
    if reuse:
        shared_stat = next((card, key, value)
                           for card in checkpoint.state.card_register.storage.get_all()
                           for key, value in card.stats.items() if id(value) in constants)
        source, key, value = shared_stat
        assert a.card_register.get_by_key(source.key).stats[key] is value
    a.players[0].health -= 7
    memories_a[0].seen["private-test"] = {"nested": [1]}
    card = next(iter(a.card_register.storage.get_all()))
    card.state.tapped = not card.state.tapped
    for _ in range(3):
        b, memories_b = checkpoint.copy()
        assert b.players[0].health == original_life
        assert "private-test" not in memories_b[0].seen
        assert memories_b[0]._game_token is b.knowledge_token
        assert b.card_register.get_by_key(card.key) is not card
        assert b.card_register.get_by_key(card.key).state.tapped != card.state.tapped
        owners = b.card_register.idx_provider.get_index_group(IK_OWNER)
        assert all(any(owner is p for p in b.players) for owner in owners)
        assert len(constants) == original_size
    assert checkpoint.state.players[0].health == original_life


def test_custom_factory_does_not_supply_unverified_copy_constants():
    def factory(controllers):
        return tactical_scenario("attack_pacified")(controllers)
    factory.immutable_values = {id(factory): factory}  # Not a native prepared world.
    sim = CheckpointSimulator(factory, checkpoint_interval=1, track_information=True,
                              policy_for=tactical_policy)
    def choose(node):
        if len(node.history) and node.decision.kind == "PriorityDecisionRequest":
            return None
        return node.player_information().actions[0]
    sim.follow_actions(choose)
    assert sim._checkpoints
    assert all(not c._immutable_values for c in sim._checkpoints.values())
