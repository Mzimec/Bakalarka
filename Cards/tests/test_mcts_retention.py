"""Pure evaluation caches, connected retention, and owned continuation copies."""
from dataclasses import replace
from random import Random
from unittest.mock import patch

import pytest

from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.search_tree_memory import SearchTreeMemory, TreeRow, RetainedNode, EdgeTable, EdgeStatistics
from game.ai.structured_values import StructuredJSON
from runtime_write_guard import deny_writes


def info_tree():
    from test_information_set_search import Tree
    return Tree({'root':{'children':{'a':'win','b':'loss'}},'win':{'winner':0},'loss':{'winner':1}})


def node(info, visits=10, horizon=(1,0)):
    return RetainedNode(info,EdgeTable((a,EdgeStatistics(visits,visits*.25)) for a in info.actions),
                        {a:1/len(info.actions) for a in info.actions},horizon)


def test_retention_pins_root_and_connecting_paths_under_both_budgets():
    root=info_tree().start().player_information()
    child=replace(root,recall=root.recall.append('action',{'go':1}))
    leaf=replace(root,recall=child.recall.append('action',{'go':2}))
    nodes={i.fast_key:node(i) for i in (leaf,child,root)}  # Adversarial insertion order.
    nodes[root.fast_key].children[child.fast_key]=root.actions[0]
    nodes[child.fast_key].children[leaf.fast_key]=child.actions[0]
    memory,token=SearchTreeMemory(),object()
    with deny_writes():
        memory.commit(token,'model',nodes,root.fast_key,2)
        assert set(memory.nodes)=={root.fast_key,child.fast_key}
        assert memory.nodes[root.fast_key] is nodes[root.fast_key]
        assert memory.nodes[child.fast_key].children=={}
        assert 0<memory.charged_bytes<=16*1024*1024
        memory.commit(token,'model',nodes,root.fast_key,3,byte_limit=1)
        assert not memory.nodes and memory.charged_bytes==0


def test_reroot_prunes_siblings_and_retains_pure_work_when_horizon_changes():
    root=info_tree().start().player_information()
    child=replace(root,recall=root.recall.append('action',{'go':1}))
    sibling=replace(root,recall=root.recall.append('action',{'go':2}))
    nodes={i.fast_key:node(i) for i in (root,child,sibling)}
    nodes[root.fast_key].children={i.fast_key:root.actions[n] for n,i in enumerate((child,sibling))}
    memory,token=SearchTreeMemory(),object()
    memory.commit(token,'model',nodes,root.fast_key,3)
    kept=memory.nodes[child.fast_key]
    assert not memory.load(token,'model',child,horizon=(4,0))
    assert memory.last_reason=='horizon_changed' and not memory.statistics_compatible
    assert memory.nodes=={child.fast_key:kept} and kept.edges.visits==0
    assert kept.probabilities and kept.information is child
    memory.load(object(),'model',child)
    assert memory.last_reason=='session_changed' and not memory.nodes


@pytest.mark.parametrize('selection',['uct','puct','hierarchical_puct'])
def test_real_search_reuses_objects_and_discards_only_horizon_dependent_counts(selection):
    from test_information_set_search import first
    root=info_tree().start().player_information()
    search=InformationSetSearch(opponent_policy=first,selection=selection,tree_reuse_size=8,
        budget=InformationSearchBudget(worlds=1,iterations=8,max_depth=1))
    token=object()
    with deny_writes():
        cold=search.search(lambda seed:info_tree(),root,reuse_context=token,seed=7)
        retained=search.tree_memory.nodes[root.fast_key]
        edges,hierarchy=retained.edges,retained.hierarchy
        warm=search.search(lambda seed:info_tree(),root,reuse_context=token,seed=8)
        assert search.tree_memory.nodes[root.fast_key] is retained and retained.edges is edges
        assert warm.metrics.reused_root_visits==8 and edges.visits==16
        if hierarchy is not None:
            assert retained.hierarchy is hierarchy
        retained.horizon=(0,0)
        structural=search.search(lambda seed:info_tree(),root,reuse_context=token,seed=7)
    assert structural.estimates==cold.estimates and structural.choice==cold.choice
    assert structural.metrics.reused_structure_nodes==1 and structural.metrics.reused_root_visits==0
    assert dict(structural.metrics.reuse_reasons)=={'horizon_changed':1}
    if selection!='uct':
        assert structural.metrics.prior_evaluations==0 and structural.metrics.reused_prior_nodes==1


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
@pytest.mark.parametrize('keep_statistics', [False, True])
def test_rerooted_candidate_prefix_cannot_select_stale_retained_actions(selection, keep_statistics):
    from game.ai.information_set_search import _Run
    from game.ai.action_hierarchy import ActionHierarchy
    from test_information_set_search import Tree, first
    tree = Tree({'root': {'children': {f'a{i}': 'win' for i in range(8)}}, 'win': {'winner': 0}})
    full = replace(tree.start().player_information(), candidate_model='test-frontier', candidate_width=8)
    prefix = replace(full, actions=full.actions[:6], menu_complete=False, candidate_width=6)
    assert full.fast_key == prefix.fast_key
    planner = InformationSetSearch(opponent_policy=first, selection=selection, tree_reuse_size=16,
        widening_constant=8, force_initial_coverage=False)
    key = prefix.fast_key
    planner.tree_memory.root_key = key
    run = _Run(planner, 0, 42)
    retained = node(full, visits=2, horizon=(0, 0))
    retained.probabilities = {action: 1/8 for action in full.actions}
    run.persistent[key] = retained
    run.loaded_menus.add(key)
    run.priors[key] = retained.probabilities
    # Put a stale action first to make the old failure deterministic.
    run.candidate_orders[key] = tuple(reversed(full.actions))
    if selection == 'hierarchical_puct':
        run.hierarchies[key] = ActionHierarchy(full.actions, retained.probabilities)
    run.reused_priors = 1
    if keep_statistics:
        run.table[key] = retained.edges
        run.loaded_keys.add(key)
        run.horizons[key] = (0, 0)
        run.reused_nodes, run.reused_root_visits = 1, retained.edges.visits
    with deny_writes():
        _, action, _ = run.tree_action(prefix)
    assert action in prefix.actions
    assert set(run.table[key]) == set(prefix.actions)
    assert set(run.candidate_orders[key]) == set(prefix.actions)
    assert run.reused_root_visits == 0 and run.reused_nodes == 0
    assert run.reused_priors == 0
    if selection != 'uct':
        assert set(run.priors[key]) == set(prefix.actions)
    assert retained.information == prefix


def test_retention_transport_keeps_links_and_no_sampled_worlds():
    root=info_tree().start().player_information()
    child=replace(root,recall=root.recall.append('action',{'go':1}))
    nodes={i.fast_key:node(i) for i in (root,child)}
    nodes[root.fast_key].children[child.fast_key]=root.actions[0]
    source=SearchTreeMemory()
    source.commit(object(),'model',nodes,root.fast_key,4)
    rows,links,key=source.export()
    other=SearchTreeMemory()
    other.save(object(),'model',rows,4,links=links,root=key)
    assert other.nodes[key].children==nodes[key].children
    assert other.rows==source.rows


@pytest.mark.parametrize('selection',['uct','puct','hierarchical_puct'])
def test_changed_belief_retains_structure_but_never_reuses_old_outcomes(selection):
    from test_information_set_search import first
    root=info_tree().start().player_information()
    planner=InformationSetSearch(opponent_policy=first,selection=selection,tree_reuse_size=8,
        budget=InformationSearchBudget(worlds=1,iterations=8,max_depth=1))
    token=object()
    with deny_writes():
        before=planner.search(lambda seed:info_tree(),root,seed=42,reuse_context=token)
        retained=planner.tree_memory.nodes[root.fast_key]
        planner.invalidate_belief()
        assert retained.edges.visits==0
        changed=planner.search(lambda seed:info_tree(),root,seed=42,reuse_context=token)
    assert changed.estimates==before.estimates
    assert changed.metrics.reused_root_visits==changed.metrics.reused_information_nodes==0
    assert changed.metrics.reused_structure_nodes==1
    assert dict(changed.metrics.reuse_reasons)=={'belief_changed':1}
    assert planner.tree_memory.nodes[root.fast_key] is retained


@pytest.mark.parametrize('backend',['thread','process'])
def test_parallel_belief_invalidation_zeroes_every_worker_slot(backend):
    from test_parallel_search import factory,planner
    from game.ai.parallel_search import ParallelInformationSetSearch
    search=planner(tree_reuse_size=8)
    info=factory(0).start().player_information()
    token=object()
    with ParallelInformationSetSearch(search,workers=2,backend=backend) as parallel:
        first=parallel.search(factory,info,seed=42,reuse_context=token)
        assert not first.used_fallback
        parallel.invalidate_belief()
        assert all(n==0 and w==0 for rows,links,root in parallel._rows for row in rows for a,n,w in row.statistics)
        second=parallel.search(factory,info,seed=42,reuse_context=token)
    assert second.estimates==first.estimates and second.metrics.reused_root_visits==0
    assert second.metrics.reused_structure_nodes>0


def test_neural_cache_reuses_exact_view_menu_and_immutable_outputs():
    pytest.importorskip('numpy')
    from test_self_play import position
    from game.ai.training_model import PolicyValueModel, NeuralPrior, NeuralValue
    from game.ai.training_features import encode_training
    info,_=position()
    model=PolicyValueModel(hidden=4,seed=7)
    expected=PolicyValueModel(hidden=4,seed=7,cache_size=0)
    with deny_writes(),patch('game.ai.training_features.encode_training',wraps=encode_training) as encode:
        prior=NeuralPrior(model)(info)
        assert prior==NeuralPrior(expected)(info)
        baseline_calls=encode.call_count
        assert NeuralPrior(model)(info)==prior and encode.call_count==baseline_calls
        value=NeuralValue(model).evaluate(info.current.observation())
        assert value==NeuralValue(expected).evaluate(info.current.observation())
        with patch.object(model,'value',side_effect=AssertionError('Repeated value inference')):
            assert NeuralValue(model).evaluate(info.current.observation())==value
        assert model.encode_information(info)==encode_training(info)
        new_recall=replace(info,recall=info.recall.append('event',{'new':True}))
        assert NeuralPrior(model)(new_recall)==prior  # Pure encoder-v1 input unchanged.
        reordered=replace(info,actions=tuple(reversed(info.actions)))
        assert NeuralPrior(model)(reordered)==pytest.approx(prior)
    assert model.cache.counts['prediction_hits']>=2 and model.cache.counts['value_hits']>=1
    assert model.cache.bytes<=model.cache.byte_limit and len(model.cache.rows)<=model.cache.entries


def test_training_invalidates_predictions_but_preserves_weight_independent_features():
    pytest.importorskip('numpy')
    from test_self_play import position, example
    from game.ai.training_model import PolicyValueModel, NeuralPrior
    info,_=position()
    row=example()
    model=PolicyValueModel(hidden=4)
    NeuralPrior(model)(info)
    menu_key=next(k for k in model.cache.rows if k[0]=='menu')
    stored=model.cache.rows[menu_key].value
    snapshot=model.to_bytes()
    with deny_writes():
        model.train_batch((row,))
        assert not any(k[0] in ('prediction','value') for k in model.cache.rows)
        assert model.cache.rows[menu_key].value is stored
        NeuralPrior(model)(info)
        fresh=PolicyValueModel.from_bytes(model.to_bytes())
        assert NeuralPrior(model)(info)==NeuralPrior(fresh)(info)
        assert PolicyValueModel.from_bytes(snapshot).step==0 and model.step==1
    import cloudpickle
    transported=cloudpickle.loads(cloudpickle.dumps(model))
    assert not transported.cache.rows and transported.revision==model.revision


def test_neural_cache_rejects_changed_private_view_and_can_be_disabled():
    pytest.importorskip('numpy')
    from test_self_play import position
    from game.ai.training_model import PolicyValueModel, NeuralPrior
    info,_=position()
    model=PolicyValueModel(hidden=4)
    NeuralPrior(model)(info)
    view=info.current.observation()
    view['players'][0]['life']-=1
    altered=replace(info,current=replace(info.current,_json=StructuredJSON(view)))
    NeuralPrior(model)(altered)
    assert model.cache.counts['prediction_misses']==2
    model.configure_cache(0,0)
    NeuralPrior(model)(info)
    NeuralPrior(model)(info)
    assert not model.cache.rows


@pytest.mark.parametrize('settings',[{'tree_reuse_bytes':True},{'tree_reuse_bytes':-1},
    {'neural_cache_size':True},{'neural_cache_bytes':-1},{'checkpoint_interval':0},
    {'checkpoint_min_visits':0},{'checkpoint_continuous':1}])
def test_invalid_retention_limits_are_rejected(settings):
    from game.simulation.search_config import SearchAgentConfig
    with pytest.raises(ValueError):
        SearchAgentConfig(deck_knowledge='both_lists',**settings)


def test_hot_continuous_checkpoints_preserve_every_view_recall_rng_and_branch():
    from test_current_position_worlds import position, prepare
    from game.ai.current_position_worlds import _PreparedWorld
    from game.ai.search_simulator import SearchSimulator
    from game.ai.checkpoint_simulator import CheckpointSimulator
    from benchmarks.search_tactics import tactical_policy
    origin,game=position()
    _,_,snapshot=prepare(origin,game)
    options=dict(track_information=True,structured_records=True,policy_for=tactical_policy,max_phases=16)
    strict=SearchSimulator(_PreparedWorld(snapshot,7,reuse=True),**options)
    fast=CheckpointSimulator(_PreparedWorld(snapshot,7,reuse=True),checkpoint_limit=4,
        checkpoint_interval=1,checkpoint_min_visits=1,**options)
    before=game.state.rng.getstate()
    traces=[]
    for sim in (strict,fast):
        seen=[]
        interior=[]
        def choose(current):
            if len(current.history)==6 and not interior:
                interior.append(current.materialize() if hasattr(current,'materialize') else current)
            seen.append(current.materialize() if hasattr(current,'materialize') else current)
            if len(current.history)>=12:
                return None
            return next((a for a in current.player_information().actions if a.kind=='PassPriorityAction'),
                        current.player_information().actions[0])
        with deny_writes():
            end=sim.follow_actions(choose,coalesce_forced=True)
            assert interior
            for _ in range(3):
                assert sim.follow_actions(choose,node=interior[0],coalesce_forced=True)==end
            assert sim.fork(end)==end
            if end.decision is not None:
                for action in end.player_information().actions[:2]:
                    assert sim.advance_action(end,action).history[:len(end.history)]==end.history
        traces.append((seen,end))
    assert traces[0]==traces[1] and fast.checkpoint_copies>0 and fast.checkpoint_hits>0
    assert 0<len(fast._checkpoints)<=4 and len(fast._boundary_visits)<=8
    assert game.state.rng.getstate()==before


def test_native_construction_reroot_retains_visits_and_objects_with_fresh_worlds():
    from benchmarks.search_tactics import tactical_scenario
    from game.ai.search_simulator import SearchSimulator
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    from game.simulation.search_config import SearchGenerationPolicy
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    builds=[]
    def scenario(controllers):
        game=tactical_scenario('useful_pacifism')(controllers)
        owner=game.state.players[1]
        for _ in range(2):
            owner.add_card(Card(starter_catalog()['Impassioned Orator'],owner),ZoneType.BATTLEFIELD)
        builds.append(True)
        return game
    def simulator():
        return SearchSimulator(scenario,track_information=True,structured_records=True,
                               policy_for=SearchGenerationPolicy(True,True))
    observer=ObservationOpponentPolicy()
    planner=InformationSetSearch(opponent_policy=observer,rollout_policy=observer,
        selection='puct',action_prior=HeuristicActionPrior(observer),tree_reuse_size=128,
        compress_forced=True,require_root_coverage=False,force_initial_coverage=False,
        budget=InformationSearchBudget(worlds=1,iterations=32,max_depth=1,max_advances=1024))
    reference=simulator()
    root=reference.start()
    token=object()
    with deny_writes():
        first=planner.search(lambda seed:simulator(),root.player_information(),seed=42,reuse_context=token)
        assert not first.used_fallback and first.choice.kind=='PrioritySourceOption'
        child=reference.advance_action(root,first.choice)
        assert child.decision.kind=='AbilityConstructionRequest'
        retained=planner.tree_memory.nodes[child.player_information().fast_key]
        class FreshChild:
            def __init__(self):
                self.simulation_backend=simulator()
            def start(self):
                sim=self.simulation_backend
                return sim.advance_action(sim.start(),first.choice)
        before=len(builds)
        second=planner.search(lambda seed:FreshChild(),child.player_information(),seed=43,reuse_context=token)
    assert not second.used_fallback and second.metrics.reused_root_visits>0
    assert second.metrics.iterations_completed==32 and len(builds)>before
    assert planner.tree_memory.nodes[child.player_information().fast_key] is retained


def test_unsupported_checkpoint_copy_skips_cache_without_changing_trajectory():
    from benchmarks.search_tactics import tactical_scenario,tactical_policy
    from game.ai.checkpoint_simulator import CheckpointSimulator,_Checkpoint
    from game.ai.runtime_clone import UnsupportedSnapshot
    sim=CheckpointSimulator(tactical_scenario('answered_shock'),track_information=True,
        policy_for=tactical_policy,checkpoint_limit=4,checkpoint_interval=1,checkpoint_min_visits=1)
    interior=[]
    def choose(current):
        if len(current.history)==10 and not interior:
            interior.append(current)
        if len(current.history)>=20:
            return None
        return next((a for a in current.player_information().actions if a.kind=='PassPriorityAction'),
                    current.player_information().actions[0])
    end=sim.follow_actions(choose)
    with deny_writes(),patch.object(_Checkpoint,'freeze',side_effect=UnsupportedSnapshot('test adapter')):
        assert sim.follow_actions(choose,node=interior[0])==end
    assert sim.checkpoint_copy_rejections==1 and sim.checkpoint_copies==0
