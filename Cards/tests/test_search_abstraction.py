"""Lossless policy reuse must preserve ratings and every exact search boundary."""
from dataclasses import replace
from collections.abc import Mapping
import pickle
from random import Random
from unittest.mock import patch

import pytest

from benchmarks.search_tactics import CASES
from game.ai.action_priors import HeuristicActionPrior
from game.ai.exact_policy_memory import ExactPolicyMemory
from game.ai.observation_policy import ObservationOpponentPolicy, remembered_counter_sources
from game.ai.search_abstraction import CanonicalPlayerView, CanonicalPolicyView
from game.ai.search_actions import PlayerAction
from game.ai.search_recall import PlayerRecall
from game.ai.structured_values import StructuredJSON
from runtime_write_guard import deny_writes
from test_observation_policy import scenario


def reference_features(value):
    if isinstance(value, Mapping):
        if 'features' in value:
            yield value['features']
        for key, child in value.items():
            if key != 'features':
                yield from reference_features(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from reference_features(child)


def reference_counter_sources(info, view):
    wanted = {row['card_id']: row['name'] for row in (view.get('knowledge') or {}).get('known_locations', ())
              if row['zone'] == 'HAND' and row['owner'] != view['self']}
    result = []
    for entry in reversed(info.recall.entries):
        if entry.kind != 'observation':
            continue
        for zone in ('stack', 'battlefield', 'graveyard', 'exile'):
            for card in entry.readonly().get('view', {}).get(zone, ()):
                identity = card.get('id')
                if identity not in wanted or card.get('name') != wanted[identity] or not card.get('identity_visible', True):
                    continue
                wanted.pop(identity)
                if any(f.get('kind') == 'counter' for f in reference_features(card.get('abilities', ()))):
                    result.append(identity)
        if not wanted:
            break
    return tuple(sorted(result))


def changed_view(info, **fields):
    return replace(info, current=replace(info.current, _json=StructuredJSON({**info.current.readonly(), **fields})))


@pytest.mark.parametrize('case', CASES)
def test_complete_player_view_roundtrips_without_discarding_any_fact(case):
    info = scenario(case)[1].player_information()
    view = info.current.observation()
    view['unknown_extension'] = {'card_id': 'old-historical-card', 'rules': ['unchanged', {'amount': 7}],
                                 'literal': '\x00card:0', 'card': '\x00card:0'}
    info = replace(info, current=replace(info.current, _json=StructuredJSON(view)))
    normal = CanonicalPlayerView(info)
    assert normal.restore() == view
    assert normal.value.readonly['unknown_extension']['literal'] == '\x00card:0'
    assert normal.key != CanonicalPolicyView(info).key
    exported = normal.restore()
    exported['unknown_extension']['rules'].append('edited')
    assert normal.restore() == view
    assert pickle.loads(pickle.dumps(normal)).restore() == view


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('canonical_ids', [False, True])
def test_native_ratings_priors_and_local_ties_match_reference(case, canonical_ids):
    with deny_writes():
        sim, node = scenario(case)
        reference, memory = ObservationOpponentPolicy(), ExactPolicyMemory(canonical_ids=canonical_ids)
        policy = ObservationOpponentPolicy(memory=memory)
        for _ in range(12):
            if node.terminal or node.cutoff:
                break
            info = node.player_information()
            if canonical_ids:
                assert CanonicalPlayerView(info).restore() == info.current.observation()
            assert policy.rank(info) == reference.rank(info)
            assert HeuristicActionPrior(policy)(info) == HeuristicActionPrior(reference)(info)
            reversed_menu = replace(info, actions=tuple(reversed(info.actions)))
            assert policy(reversed_menu) == reference(info)
            node = sim.advance_action(node, reference(info))
        assert memory.counts['action_hits'] > 0


def test_staged_ability_construction_keeps_parameters_and_payment_exact():
    from benchmarks.search_tactics import tactical_scenario
    from game.simulation.search_config import SearchGenerationPolicy
    from game.ai.search_simulator import SearchSimulator
    sim = SearchSimulator(tactical_scenario('useful_pacifism'), track_information=True,
                          policy_for=SearchGenerationPolicy(staged_priority=True, staged_ability=True))
    node = sim.start()
    reference = ObservationOpponentPolicy()
    policy = ObservationOpponentPolicy(memory=ExactPolicyMemory(canonical_ids=True))
    kinds = set()
    with deny_writes():
        for _ in range(12):
            if node.terminal or node.cutoff:
                break
            info = node.player_information()
            kinds.add(info.current.request_kind)
            assert CanonicalPlayerView(info).restore() == info.current.observation()
            assert policy.rank(info) == reference.rank(info)
            node = sim.advance_action(node, reference(info))
    assert 'PriorityAbilityRequest' in kinds or 'AbilityConstructionRequest' in kinds


def test_different_histories_can_share_ratings_without_sharing_search_identity():
    info = scenario('useful_pacifism')[1].player_information()
    later = replace(info, recall=info.recall.append('event', {'kind': 'unrelated', 'turn': 2}))
    assert info.fast_key != later.fast_key
    memory = ExactPolicyMemory()
    policy = ObservationOpponentPolicy(memory=memory)
    with deny_writes():
        expected = policy.rank(info)
        assert policy.rank(later) == expected
    assert memory.counts['view_hits'] == 1
    assert memory.counts['action_misses'] == len(info.actions)
    assert memory.counts['action_hits'] == len(info.actions)
    assert later.recall.entries[-1].readonly()['kind'] == 'unrelated'


def test_policy_sufficient_input_is_not_a_world_or_tree_transposition_key():
    info = scenario('useful_pacifism')[1].player_information()
    view = info.current.observation()
    view.update(turn=17, phase='POSTCOMBAT_MAIN', priority=1, passed_players=[0])
    view['knowledge']['recent_events'].append({'kind': 'public_history_marker'})
    other = replace(info, current=replace(info.current, _json=StructuredJSON(view)))
    assert other.fast_key != info.fast_key
    memory = ExactPolicyMemory()
    policy = ObservationOpponentPolicy(memory=memory)
    assert policy.rank(info) == ObservationOpponentPolicy().rank(info)
    assert policy.rank(other) == ObservationOpponentPolicy().rank(other)
    assert memory.counts['action_misses'] == len(info.actions)
    assert memory.counts['action_hits'] == len(info.actions)


def test_irrelevant_nested_names_do_not_get_interpreted_as_combat_references():
    info = scenario('useful_pacifism')[1].player_information()
    view = info.current.observation()
    view['decision_context'] = {'custom': {'attackers': [{'card': 'unknown'}], 'blockers': [1, 2]}}
    other = replace(info, current=replace(info.current, _json=StructuredJSON(view)))
    policy = ObservationOpponentPolicy(memory=ExactPolicyMemory(canonical_ids=True))
    assert policy.rank(other) == ObservationOpponentPolicy().rank(other)


def test_cached_scores_never_allow_rerooting_on_a_different_full_recall():
    from game.ai.search_tree_memory import SearchTreeMemory, TreeRow
    info = scenario('useful_pacifism')[1].player_information()
    other = replace(info, recall=info.recall.append('event', {'kind': 'different_history'}))
    policy = ObservationOpponentPolicy(memory=ExactPolicyMemory())
    assert policy.rank(info) == policy.rank(other)
    memory, session = SearchTreeMemory(), object()
    memory.save(session, 'same_model', (TreeRow(info, tuple((a, 100, 50.) for a in info.actions), ()),), 8)
    assert not memory.load(session, 'same_model', other)
    assert memory.last_reason == 'root_missing' and not memory.nodes


def test_cached_opponent_ratings_do_not_learn_root_private_hand():
    from benchmarks.search_hidden_worlds import beliefs, catalog, public_position
    from benchmarks.search_tactics import tactical_policy
    from game.ai.sampled_scenario import SampledScenario
    from game.ai.search_simulator import SearchSimulator
    policy = ObservationOpponentPolicy(memory=ExactPolicyMemory())
    inputs, outputs = [], []
    for name in ('Shock', 'Plains'):
        own, other = beliefs()
        factory = SampledScenario(public_position, catalog, (replace(own, known_hand=(name,)), other), observer=0, seed=1)
        sim = SearchSimulator(factory, policy_for=tactical_policy, track_information=True)
        root = sim.start()
        policy.rank(root.player_information())
        reply = sim.advance_action(root, next(a for a in root.player_information().actions if a.kind == 'PassPriorityAction'))
        info = reply.player_information()
        inputs.append(info)
        outputs.append(policy.rank(info))
        assert outputs[-1] == ObservationOpponentPolicy().rank(info)
    assert inputs[0] == inputs[1] and outputs[0] == outputs[1]


def test_production_configuration_owns_one_policy_memory_and_reset_releases_it():
    from game.simulation.search_config import make_search_controller
    from game.ai.game_origin import GameOrigin
    from game.cards.decks import load_arena_starter
    from game.cards.catalog import game_catalog
    origin = GameOrigin((load_arena_starter('red'), load_arena_starter('white')), game_catalog)
    controller = make_search_controller({'deck_knowledge': 'both_lists', 'selection': 'puct', 'prior': 'heuristic',
                                         'exact_policy_cache_size': 8, 'exact_policy_cache_bytes': 16777216},
                                        origin, max_decisions=100)
    search = controller.search
    assert search.opponent_policy is search.rollout_policy is search.action_prior.policy
    memory = search.opponent_policy.memory
    info = scenario('useful_pacifism')[1].player_information()
    search.opponent_policy.rank(info)
    assert memory.rows
    search.reset()
    assert memory.bytes == 0 and not memory.rows


def test_widening_only_scores_new_complete_actions_and_never_reuses_softmax():
    info = scenario('useful_pacifism')[1].player_information()
    memory = ExactPolicyMemory()
    policy = ObservationOpponentPolicy(memory=memory)
    prefix = replace(info, actions=info.actions[:1], menu_complete=False, candidate_model='test', candidate_width=1)
    policy.rank(prefix)
    assert policy.rank(info) == ObservationOpponentPolicy().rank(info)
    assert memory.counts['action_misses'] == len(info.actions)
    assert memory.counts['action_hits'] == 1
    assert HeuristicActionPrior(policy)(info) == HeuristicActionPrior()(info)


def relabeled(info, prefix):
    original = CanonicalPolicyView(info)
    labels = {identity: prefix + str(index) for index, identity in enumerate(original.bindings)}
    def rewrite(value, context=None):
        from collections.abc import Mapping
        if isinstance(value, Mapping):
            if context in ('defenders', 'blockers'):
                return {labels.get(key, key): (labels.get(child, child) if context == 'blockers' else rewrite(child))
                        for key, child in value.items()}
            row = 'zone_revision' in value and 'id' in value
            return {key: (labels.get(child, child) if key == 'card' or key == 'id' and row
                          else rewrite(child, key)) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            if context in ('attackers', 'blocked'):
                return [labels.get(child, child) for child in value]
            return [rewrite(child) for child in value]
        return value
    current = replace(info.current, _json=StructuredJSON(rewrite(info.current.readonly())))
    actions = tuple(PlayerAction(action.kind, StructuredJSON(rewrite(action.readonly()))) for action in info.actions)
    return replace(info, current=current, actions=actions)


@pytest.mark.parametrize('case', CASES)
def test_consistent_identity_relabeling_reuses_ratings_but_binds_new_actions(case):
    info = scenario(case)[1].player_information()
    other = relabeled(info, 'other-card-')
    a, b = CanonicalPolicyView(info), CanonicalPolicyView(other)
    assert a.key == b.key
    assert tuple(map(a.action, info.actions)) == tuple(map(b.action, other.actions))
    memory = ExactPolicyMemory(canonical_ids=True)
    policy = ObservationOpponentPolicy(memory=memory)
    policy.rank(info)
    assert policy.rank(other) == ObservationOpponentPolicy().rank(other)
    assert memory.counts['action_misses'] == len(info.actions)
    assert memory.counts['action_hits'] == len(other.actions)
    assert all(row.action in other.actions for row in policy.rank(other))


@pytest.mark.parametrize('field,value', [('power', 9), ('zone_revision', 3), ('tapped', True),
                                       ('face_down', True), ('damage_marked', 2),
                                       ('keywords', ['first strike']), ('attached_to', {'card': 'another', 'zone_revision': 1})])
def test_tactical_characteristics_and_incarnations_do_not_merge(field, value):
    info = scenario('attack_pacified')[1].player_information()
    view = info.current.observation()
    view['battlefield'][0][field] = value
    other = replace(info, current=replace(info.current, _json=StructuredJSON(view)))
    assert CanonicalPolicyView(info).key != CanonicalPolicyView(other).key


def test_targets_and_revision_are_part_of_complete_action_identity():
    info = scenario('useful_pacifism')[1].player_information()
    context = CanonicalPolicyView(info)
    action = PlayerAction('Test', StructuredJSON({'target': {'card': next(iter(context.bindings)), 'zone_revision': 1}}))
    stale = PlayerAction('Test', StructuredJSON({'target': {'card': next(iter(context.bindings)), 'zone_revision': 2}}))
    unknown = PlayerAction('Test', StructuredJSON({'target': {'card': '\x00card:0', 'zone_revision': 1}}))
    assert context.action(action) != context.action(stale)
    assert context.action(action) != context.action(unknown)


def test_index_matches_reverse_history_with_negative_hidden_and_changed_name_evidence():
    info = scenario('useful_pacifism')[1].player_information()
    rng = Random(311)
    recall = PlayerRecall()
    for index in range(100):
        cards = [{'id': str(rng.randrange(5)), 'name': 'Name' + str(rng.randrange(2)),
                  'identity_visible': bool(rng.randrange(3)),
                  'abilities': [{'features': {'kind': rng.choice(('counter', 'draw', 'unknown'))}}]}
                 for _ in range(4)]
        recall = recall.append('observation', {'view': {rng.choice(('stack', 'battlefield', 'hand')): cards}})
        locations = [{'card_id': str(i), 'name': 'Name' + str(rng.randrange(2)), 'zone': 'HAND', 'owner': 1} for i in range(5)]
        view = {'self': 0, 'knowledge': {'known_locations': locations}}
        current = replace(info, recall=recall)
        assert remembered_counter_sources(current, view) == reference_counter_sources(current, view)
        # Requesting the index starts incremental propagation on later appends.
        recall.knowledge
    restored = pickle.loads(pickle.dumps(recall))
    assert restored == recall and restored.knowledge.evidence == recall.knowledge.evidence
    assert remembered_counter_sources(replace(info, recall=restored), view) == reference_counter_sources(current, view)
    view['knowledge']['known_locations'] = []
    assert remembered_counter_sources(current, view) == ()


def test_counter_evidence_changes_cache_context_and_forgetting_restores_original():
    info = scenario('useful_pacifism')[1].player_information()
    history = {'stack': [{'id': 'known', 'name': 'Counterspell', 'abilities': [{'features': {'kind': 'counter'}}]}]}
    recall = info.recall.append('observation', {'view': history})
    view = info.current.observation()
    view['knowledge']['known_locations'].append({'card_id': 'known', 'name': 'Counterspell', 'owner': 1, 'zone': 'HAND'})
    other = replace(info, current=replace(info.current, _json=StructuredJSON(view)), recall=recall)
    memory = ExactPolicyMemory()
    policy = ObservationOpponentPolicy(memory=memory)
    base = policy.rank(info)
    assert policy.rank(other) == ObservationOpponentPolicy().rank(other)
    assert policy.rank(other) != base
    assert len(memory.rows) == 2
    assert policy.rank(replace(info, recall=recall)) == base


def test_cache_limits_eviction_pickle_and_custom_evaluator_bypass():
    from game.ai.state_value import StateValueEvaluator
    info = scenario('useful_pacifism')[1].player_information()
    memory = ExactPolicyMemory(entries=1)
    policy = ObservationOpponentPolicy(memory=memory)
    with deny_writes():
        policy.rank(info)
        other = changed_view(info, players=[{**row, 'life': row['life'] - 1} for row in info.current.readonly()['players']])
        policy.rank(other)
        assert len(memory.rows) == 1 and memory.counts['evictions'] == 1
        assert memory.bytes <= memory.byte_limit
        restored = pickle.loads(pickle.dumps(policy))
        assert not restored.memory.rows and restored.memory.bytes == 0
        assert restored.memory.entries == memory.entries
        assert restored.rank(other) == policy.rank(other)
        tiny = ExactPolicyMemory(byte_limit=1)
        assert ObservationOpponentPolicy(memory=tiny).rank(info) == ObservationOpponentPolicy().rank(info)
        assert not tiny.rows and tiny.bytes == 0
        class Custom(StateValueEvaluator):
            pass
        custom = ObservationOpponentPolicy(Custom(), memory=memory)
        with patch.object(memory, 'rank', side_effect=AssertionError('custom evaluator must bypass')):
            assert custom.rank(info) == ObservationOpponentPolicy(Custom()).rank(info)


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
@pytest.mark.parametrize('canonical_ids', [False, True])
def test_native_search_same_work_same_results_and_no_approximate_visits(selection, canonical_ids):
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.search_simulator import SearchSimulator
    from benchmarks.search_tactics import tactical_scenario, tactical_policy
    from benchmarks.mcts_decision_latency import answer
    def factory(seed):
        return SearchSimulator(tactical_scenario('useful_pacifism'), policy_for=tactical_policy, track_information=True)
    info = factory(0).start().player_information()
    def planner(memory):
        policy = ObservationOpponentPolicy(memory=memory)
        return InformationSetSearch(opponent_policy=policy, rollout_policy=policy, selection=selection,
            action_prior=HeuristicActionPrior(policy) if selection != 'uct' else None,
            budget=InformationSearchBudget(worlds=2, iterations=24, max_depth=4, max_advances=2048),
            force_initial_coverage=False, require_root_coverage=False)
    search = planner(ExactPolicyMemory(canonical_ids=canonical_ids))
    with deny_writes():
        reference = planner(None).search(factory, info, seed=77)
        result = search.search(factory, info, seed=77)
    assert answer(reference) == answer(result)
    assert result.metrics.policy_action_hits > 0
    assert result.metrics.policy_action_misses > 0
    assert result.metrics.reused_root_visits == 0
    assert result.metrics.iterations_completed == reference.metrics.iterations_completed
    assert search.opponent_policy.memory.rows
    search.invalidate_belief()
    assert search.opponent_policy.memory.rows  # Pure local ratings have no sampled outcome dependency.
    search.invalidate_model()
    assert not search.opponent_policy.memory.rows


@pytest.mark.parametrize('backend', ['thread', 'process'])
def test_parallel_workers_keep_exact_results_with_locally_owned_memory(backend):
    from game.ai.parallel_search import ParallelInformationSetSearch
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from test_parallel_search import factory
    info = factory(0).start().player_information()
    results = []
    for enabled in (False, True):
        policy = ObservationOpponentPolicy(memory=ExactPolicyMemory() if enabled else None)
        search = InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
            selection='puct', action_prior=HeuristicActionPrior(policy), require_root_coverage=False,
            force_initial_coverage=False, budget=InformationSearchBudget(worlds=2, iterations=16, max_depth=3))
        with ParallelInformationSetSearch(search, workers=2, backend=backend) as parallel:
            results.append(parallel.search(factory, info, seed=17))
    assert results[0].estimates == results[1].estimates
    assert results[0].choice == results[1].choice
    assert results[0].metrics.advance_attempts == results[1].metrics.advance_attempts
    assert results[1].metrics.policy_action_misses > 0


@pytest.mark.parametrize('settings', [{'exact_policy_cache_size': True}, {'exact_policy_cache_size': -1},
                                     {'exact_policy_cache_bytes': -1}, {'exact_policy_cache_bytes': 1.5},
                                     {'exact_policy_canonical_ids': 1}])
def test_invalid_configuration(settings):
    from game.simulation.search_config import SearchAgentConfig
    with pytest.raises(ValueError):
        SearchAgentConfig.parse({'deck_knowledge': 'both_lists', **settings})
