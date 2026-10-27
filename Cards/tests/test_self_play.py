"""Numerical gradients, visibility-safe targets and real spawned game actors."""
from dataclasses import replace
import json
from io import BytesIO
from pathlib import Path
from random import Random
from types import SimpleNamespace

import pytest

np = pytest.importorskip('numpy')
from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.search_simulator import SearchSimulator
from game.ai.search_actions import canonical_json
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.self_play_config import SelfPlayConfig
from game.ai.self_play_data import EpisodeRecorder, TrainingExample, ReplayBuffer, visit_policy
from game.ai.training_features import encode_training, STATE_SIZE, ACTION_SIZE
from game.ai.training_model import PolicyValueModel, NeuralPrior, NeuralValue


def position():
    info = SearchSimulator(tactical_scenario('lethal_shock'),policy_for=tactical_policy,track_information=True).start().player_information()
    policy = ObservationOpponentPolicy()
    planner = InformationSetSearch(opponent_policy=policy,rollout_policy=policy,
        require_root_coverage=False,compress_forced=True,
        budget=InformationSearchBudget(worlds=1,iterations=8,max_depth=2,max_advances=128))
    result = planner.search(lambda seed:SearchSimulator(tactical_scenario('lethal_shock'),
        policy_for=tactical_policy,track_information=True),info)
    assert not result.used_fallback
    return info,result


def example():
    info,result = position()
    recorder = EpisodeRecorder(episode='test-game',player=0,generation=0,revision='fixed',rng=Random(1),validation_fraction=0)
    recorder.choose(info,result)
    return recorder.finish(SimpleNamespace(status='win',winner_index=0))[0]


def test_targets_are_raw_visits_behavior_temperature_is_separate_and_cutoffs_unlabelled():
    info,result = position()
    recorder = EpisodeRecorder(episode='test',player=0,generation=0,revision='v1',rng=Random(4),
        temperature=0,validation_fraction=0)
    choice = recorder.choose(info,result)
    row = recorder.finish(SimpleNamespace(status='win',winner_index=1))[0]
    assert row.outcome == -1 and row.action_keys[row.selected] == choice.key
    assert row.policy == visit_policy(row.visits) and sum(row.behavior) == 1
    assert sum(p>0 for p in row.behavior) == 1
    assert not recorder.finish(SimpleNamespace(status='turn_limit',winner_index=None))
    assert recorder.skipped['nonterminal_episode'] == 1
    assert recorder.finish(SimpleNamespace(status='draw',winner_index=None))[0].outcome == 0
    assert TrainingExample.from_dict(json.loads(json.dumps(row.to_dict()))) == row


def test_censored_and_incomplete_menus_never_produce_targets_or_silently_truncate():
    info,result = position()
    recorder = EpisodeRecorder(episode='test',player=0,generation=0,revision='v1',rng=Random(1))
    recorder.choose(replace(info,menu_complete=False),result)
    recorder.choose(info,replace(result,metrics=replace(result.metrics,interrupted_trajectories=1)))
    recorder.choose(info,replace(result,metrics=replace(result.metrics,reused_root_visits=3)))
    recorder.max_training_actions = 1
    recorder.choose(info,result)
    assert not recorder.pending
    assert recorder.skipped == dict(incomplete_menu=1,censored_search=1,reused_root_visits=1,training_menu_limit=1)


def test_replay_is_bounded_and_refuses_heldout_games_and_duplicates():
    row = example()
    replay = ReplayBuffer(2)
    replay.add(row)
    with pytest.raises(ValueError,match='Duplicate'):
        replay.add(row)
    with pytest.raises(ValueError,match='training examples only'):
        replay.add(replace(row,split='validation'))
    replay.add(replace(row,episode='b'))
    replay.add(replace(row,episode='c'))
    assert [r.episode for r in replay.rows] == ['b','c'] and replay.total_seen == 3
    assert replay.sample(2,Random(1)) == replay.sample(2,Random(1))
    assert not hasattr(replay,'save')


def test_pooled_features_ignore_names_ids_and_card_row_permutations():
    info,_ = position()
    before = encode_training(info)
    def rename(value):
        if isinstance(value,dict):
            return {k:'different' if k in ('name','text','key','ability_key','action_key') else rename(v) for k,v in value.items()}
        if isinstance(value,list):
            return [rename(v) for v in value]
        if isinstance(value,str) and value.startswith('c') and value[1:].isdigit():
            return 'renamed-'+value
        return value
    view = rename(info.current.observation())
    for zone in ('hand','battlefield','stack','graveyard','exile'):
        view[zone] = list(reversed(view.get(zone,())))
    other = replace(info,current=replace(info.current,_json=canonical_json(view)),
        actions=tuple(replace(a,_json=canonical_json(rename(a.describe()))) for a in info.actions))
    assert before == encode_training(other)
    assert len(before[0]) == STATE_SIZE and all(len(a) == ACTION_SIZE for a in before[1])
    a = PolicyValueModel(hidden=4,seed=7)
    assert a.predict_batch([before])[0] == a.predict_batch([encode_training(other)])[0]


def test_model_gradients_match_finite_differences_and_learning_reduces_loss():
    row = example()
    model = PolicyValueModel(hidden=4,seed=7)
    losses,grads = model.loss_gradients([row],weight_decay=.001)
    for key,index in [('ws',(0,0)),('wa',(0,0)),('wp',(0,)),('wv',(1,)),('bs',(1,)),('ba',(0,)),('bv',(0,))]:
        old = model.weights[key][index]
        epsilon = 1e-5
        model.weights[key][index] = old+epsilon
        upper = model.loss_gradients([row],weight_decay=.001)[0]['loss']
        model.weights[key][index] = old-epsilon
        lower = model.loss_gradients([row],weight_decay=.001)[0]['loss']
        model.weights[key][index] = old
        assert grads[key][index] == pytest.approx((upper-lower)/(2*epsilon),abs=1e-6)
    for _ in range(20):
        model.train_batch([row],learning_rate=.005)
    assert model.loss_gradients([row])[0]['loss'] < losses['loss']*.7
    snapshot = model.to_bytes()
    loaded = PolicyValueModel.from_bytes(snapshot)
    assert loaded.revision == model.revision and loaded.step == 20
    assert loaded.predict_batch([(row.state,row.actions)]) == model.predict_batch([(row.state,row.actions)])
    model.train_batch([row])
    loaded.train_batch([row])
    assert loaded.revision == model.revision  # Adam state, not only weights.
    assert PolicyValueModel.from_bytes(snapshot).step == 20  # Immutable old version.
    assert not hasattr(model,'save')


def test_neural_adapters_are_menu_aligned_and_value_is_root_perspective():
    info,_ = position()
    model = PolicyValueModel(hidden=4)
    prior = NeuralPrior(model)(info)
    assert set(prior) == set(info.actions) and sum(prior.values()) == pytest.approx(1)
    features = encode_training(info)
    assert NeuralValue(model).evaluate(info.current.observation()) == model.value(features[0])
    batch = model.predict_batch([features,(features[0],features[1][:2])])
    assert batch[0] == model.predict_batch([features])[0]
    assert batch[0][1] == batch[1][1] and len(batch[1][0]) == 2


def test_ragged_batch_gradients_and_checkpoint_schema_validation():
    row = example()
    counts = row.visits[:2]
    policy = visit_policy(counts)
    other = replace(row,episode='other',outcome=-1.,actions=row.actions[:2],visits=counts,
        action_keys=row.action_keys[:2],policy=policy,behavior=policy,selected=counts.index(max(counts)))
    model = PolicyValueModel(hidden=4,seed=9)
    batch = [row,other]
    _,grads = model.loss_gradients(batch,weight_decay=0)
    for key,index in [('ws',(0,0)),('wv',(0,)),('wp',(1,))]:
        old = model.weights[key][index]
        model.weights[key][index] = old+1e-5
        upper = model.loss_gradients(batch,weight_decay=0)[0]['loss']
        model.weights[key][index] = old-1e-5
        lower = model.loss_gradients(batch,weight_decay=0)[0]['loss']
        model.weights[key][index] = old
        assert grads[key][index] == pytest.approx((upper-lower)/2e-5,abs=1e-6)
    with np.load(BytesIO(model.to_bytes()),allow_pickle=False) as archive:
        arrays = {k:archive[k] for k in archive.files}
    metadata = json.loads(str(arrays['metadata']))
    metadata['feature_version'] = 999
    arrays['metadata'] = np.array(json.dumps(metadata))
    stream = BytesIO()
    np.savez(stream,**arrays)
    with pytest.raises(ValueError,match='schema'):
        PolicyValueModel.from_bytes(stream.getvalue())


def test_neural_checkpoint_config_reaches_real_native_search_and_play_cli(tmp_path,monkeypatch):
    from game.simulation.search_config import make_search_controller
    from test_current_position_worlds import position as native_position, prepare
    from game.bin import play
    from game.simulation.match_runner import MatchResult
    import sys
    model = PolicyValueModel(hidden=4)
    checkpoint = tmp_path/'model.npz'
    checkpoint.write_bytes(model.to_bytes())  # Explicit read-only input fixture.
    origin,game = native_position()
    worlds,info,snapshot = prepare(origin,game)
    controller = make_search_controller(dict(deck_knowledge='both_lists',selection='puct',prior='neural',
        value_model='neural',model_checkpoint=str(checkpoint),worlds=1,iterations=4,max_depth=1,max_advances=128),
        origin,max_decisions=1000)
    assert isinstance(controller.search.action_prior,NeuralPrior)
    assert isinstance(controller.search.evaluator,NeuralValue)
    assert controller.search.action_prior.model is controller.search.evaluator.model
    assert controller.search.model_revision == model.revision
    assert not controller.search.search(worlds,info).used_fallback
    captured = {}
    def run(colors,output,**kwargs):
        captured.update(kwargs)
        return MatchResult(colors,42,0,'aborted',None,None,0,0,None)
    monkeypatch.setattr(play,'run_match',run)
    monkeypatch.setattr(sys,'argv',['play','--ai-agent','mcts','--mcts-known-decks',
        '--mcts-settings',str(Path(__file__).parents[1]/'configs/mcts_flat_puct_fast.json'),
        '--mcts-checkpoint',str(checkpoint)])
    assert play.main() == 0
    assert isinstance(captured['controllers'][1].search.action_prior,NeuralPrior)
    captured['controllers'][1].close_search()


def test_recorders_split_by_whole_episode_and_refuse_other_players_views():
    info,result = position()
    first = EpisodeRecorder(episode='game',player=0,generation=0,revision='v',rng=Random(1),validation_fraction=.5)
    second = EpisodeRecorder(episode='game',player=1,generation=0,revision='v',rng=Random(2),validation_fraction=.5)
    assert first.split == second.split
    with pytest.raises(ValueError,match='another player'):
        second.choose(info,result)


def test_seed_streams_do_not_feed_match_seeds_into_search():
    from game.ai.self_play import _seed
    seeds = [_seed(42,domain,'0-play-0000') for domain in ('match','search-0','search-1','behavior-0','noise-0')]
    assert len(set(seeds)) == len(seeds)
    assert seeds == [_seed(42,domain,'0-play-0000') for domain in ('match','search-0','search-1','behavior-0','noise-0')]


@pytest.mark.parametrize('settings',[
    dict(actors=0),dict(actors=True),dict(actors=62),dict(validation_fraction=1),
    dict(mcts=dict(deck_knowledge='both_lists',parallel_workers=2)),
    dict(mcts=dict(deck_knowledge='both_lists',staged_ability=True)),
    dict(mcts=dict(deck_knowledge='both_lists',max_search_ms=500)),
    dict(mcts=dict(deck_knowledge='both_lists',tree_reuse_size=32)),
])
def test_invalid_actor_training_contract_is_rejected(settings):
    with pytest.raises(ValueError):
        SelfPlayConfig(**settings)


def test_root_noise_only_changes_root_and_restores_prior_even_on_failure():
    from game.ai.self_play_search import SelfPlaySearch
    from game.ai.action_priors import UniformActionPrior
    info,_ = position()
    other = replace(info,recall=info.recall.append('event',{'different':True}))
    uniform = UniformActionPrior()
    class Search:
        selection,action_prior = 'puct',uniform
        def search(self,factory,information,**kwargs):
            self.observed = self.action_prior(information),self.action_prior(other)
            raise RuntimeError('probe')
    base = Search()
    wrapped = SelfPlaySearch(base,rng=Random(1))
    with pytest.raises(RuntimeError,match='probe'):
        wrapped.search(None,info)
    assert base.action_prior is uniform
    assert len(set(base.observed[0].values()))>1
    assert len(set(base.observed[1].values())) == 1


def test_real_spawn_actors_keep_cutoff_games_out_of_replay_and_close_pool():
    from game.ai.self_play import run_self_play
    config = SelfPlayConfig(actors=2,generations=1,games_per_generation=2,evaluation_games=0,
        max_turns=1,collect_only=True,batch_size=1,temperature=0,dirichlet_epsilon=0,
        mcts=dict(deck_knowledge='both_lists',selection='puct',prior='heuristic',
                  worlds=1,iterations=2,max_depth=1,max_extensions=16,max_advances=128))
    result = run_self_play(config)
    generation = result['generations'][0]
    assert result['status'] == 'collected' and result['source_unchanged']
    assert result['optimizer_step'] == 0 and generation['accepted_training_examples'] == 0
    assert generation['peak_inflight_games'] <= 2
    assert all(row['result']['status']=='turn_limit' and row['examples']==0 for row in generation['episodes'])
    assert not result['replay'].rows and not result['validation']
    assert result['model'].revision == result['model_revision']
    assert PolicyValueModel.from_bytes(result['model_snapshot']).revision == result['model_revision']
