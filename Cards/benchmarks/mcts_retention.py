"""RAM-only ABBA probes: neural memoization, structural reuse, native restores.

Default output is stdout. --output explicitly selects one new JSON output log;
there are no source archives, checkpoint files, temporary files or directories.
These are cost probes, not playing-strength or whole-game comparisons.
"""
import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time


def semantics(result):
    return replace(result,metrics=None)


def search_sample(component, enabled, factory, info, iterations):
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    policy=ObservationOpponentPolicy()
    search=InformationSetSearch(opponent_policy=policy,rollout_policy=policy,
        selection='puct',action_prior=HeuristicActionPrior(policy),compress_forced=True,
        force_initial_coverage=False,require_root_coverage=False,
        tree_reuse_size=128 if component=='tree' and enabled else 0,
        budget=InformationSearchBudget(worlds=2,iterations=iterations,max_depth=4,
                                      max_extensions=8,max_advances=4096))
    if component in ('neural','payload'):
        from game.ai.training_model import PolicyValueModel, NeuralPrior, NeuralValue
        model=PolicyValueModel(hidden=8,seed=7,cache_size=256 if enabled or component=='payload' else 0)
        search.action_prior,search.evaluator=NeuralPrior(model),NeuralValue(model)
        if component=='payload' and not enabled:
            class DictValue:
                def __init__(self,model):
                    self.model=model
                def evaluate(self,view):
                    return NeuralValue(self.model).evaluate(view)
            search.evaluator=DictValue(model)
    token=object()
    if component=='tree':
        # Charge preparation outside the measured second decision, on BOTH
        # variants. Deliberately invalidate horizons to compare fresh N/W.
        search.search(factory,info,seed=41,reuse_context=token)
        for node in search.tree_memory.nodes.values():
            node.horizon=(-1,-1)
    started,cpu=perf_counter(),process_time()
    result=search.search(factory,info,seed=42,reuse_context=token)
    wall_ms,cpu_ms=(perf_counter()-started)*1000,(process_time()-cpu)*1000
    return result,dict(enabled=enabled,wall_ms=wall_ms,cpu_ms=cpu_ms,metrics=asdict(result.metrics))


def checkpoint_sample(enabled, restores):
    from benchmarks.search_tactics import tactical_scenario,tactical_policy
    from game.ai.checkpoint_simulator import CheckpointSimulator
    sim=CheckpointSimulator(tactical_scenario('answered_shock'),track_information=True,
        policy_for=tactical_policy,structured_records=True,max_phases=64,checkpoint_limit=8,
        checkpoint_interval=1,checkpoint_min_visits=1,checkpoint_continuous=enabled)
    interior=[]
    def choose(node):
        if len(node.history)==10 and not interior:
            interior.append(node.materialize() if hasattr(node,'materialize') else node)
        if len(node.history)>=20:
            return None
        return next((a for a in node.player_information().actions if a.kind=='PassPriorityAction'),
                    node.player_information().actions[0])
    started,cpu=perf_counter(),process_time()
    end=sim.follow_actions(choose,coalesce_forced=True)
    # Probe continuation from an interior point reached in the first trajectory,
    # where hot snapshots can replace prefix reconstruction. Both variants
    # include the entire initial capture cost in the measured interval.
    prefix=interior[0] if interior else end
    outputs=[]
    for _ in range(restores):
        outputs.append(sim.follow_actions(choose,node=prefix,coalesce_forced=True))
    result=dict(enabled=enabled,wall_ms=(perf_counter()-started)*1000,
        cpu_ms=(process_time()-cpu)*1000,checkpoint_hits=sim.checkpoint_hits,
        checkpoint_copies=sim.checkpoint_copies,checkpoint_capture_ms=sim.checkpoint_capture_ms,
        decisions_replayed=sim.decisions_replayed,checkpoint_evictions=sim.checkpoint_evictions)
    return (end,tuple(outputs)),result


def probe(component, *, captured='red', iterations=32, repeats=4):
    from benchmarks.engine_inner import fingerprint
    cards=Path(__file__).resolve().parents[1]
    stamp=fingerprint(cards)
    script_stamp=sha256(Path(__file__).read_bytes()).hexdigest()
    factory=info=None
    if component!='checkpoint':
        from benchmarks.mcts_parallel import capture
        factory,info=capture('red' if captured=='red' else 'blue',
                             'white' if captured=='red' else 'black',42 if captured=='red' else 43,
                             9 if captured=='red' else 13)
    rows,expected,work=[],None,None
    # ABBA + BAAB; each pair's ordering reverses. Fresh planners/models each time.
    order=(False,True,True,False,True,False,False,True)
    for index in range(2*repeats):
        enabled=order[index%len(order)]
        if component=='checkpoint':
            signature,row=checkpoint_sample(enabled,iterations)
        else:
            result,row=search_sample(component,enabled,factory,info,iterations)
            signature=semantics(result)
            assert not result.used_fallback
            assert result.metrics.iterations_completed==iterations
            counters={k:row['metrics'][k] for k in ('worlds_built','iterations_started','iterations_completed',
                'advance_attempts','terminal_leaves','heuristic_leaves','interrupted_trajectories',
                'interruption_reasons','rollout_policy_choices','rollout_random_choices',
                'root_actions','root_actions_visited','forced_transitions')}
            if work is None:
                work=counters
            assert counters==work,'Optimization changed logical search work'
        if expected is None:
            expected=signature
        assert signature==expected,'Optimization changed search/continuation results'
        rows.append(row)
        brief={k:v for k,v in row.items() if k!='metrics'}
        if 'metrics' in row:
            brief.update({k:row['metrics'][k] for k in ('encoding_ms','inference_ms','neural_cache_hits',
                'reused_structure_nodes','reused_root_visits','reused_prior_nodes','reuse_charged_bytes')})
        print(json.dumps(dict(kind='sample',component=component,**brief)),flush=True)
    summary={str(enabled):dict(wall_ms=median(r['wall_ms'] for r in rows if r['enabled']==enabled),
                              cpu_ms=median(r['cpu_ms'] for r in rows if r['enabled']==enabled)) for enabled in (False,True)}
    assert stamp==fingerprint(cards),'Sources changed while measuring'
    assert script_stamp==sha256(Path(__file__).read_bytes()).hexdigest()
    return dict(component=component,captured=captured if component!='checkpoint' else None,
        source_fingerprint=stamp,source_unchanged=True,
        script_fingerprint=script_stamp,
        iterations=iterations,repeats=repeats,equivalent=True,summary=summary,samples=rows,
        wall_reduction_percent=100*(1-summary['True']['wall_ms']/summary['False']['wall_ms']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',choices=('neural','payload','tree','checkpoint'),default='neural')
    parser.add_argument('--captured',choices=('red','blue'),default='red')
    parser.add_argument('--iterations',type=int,default=32)
    parser.add_argument('--repeats',type=int,default=4)
    parser.add_argument('--output',type=Path,help='Optional new JSON output log; existing parent directory')
    args=parser.parse_args()
    if args.iterations<1 or args.repeats<2:
        parser.error('Need positive work and at least two repeats per variant')
    result=probe(args.component,captured=args.captured,iterations=args.iterations,repeats=args.repeats)
    if args.output is not None:
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,allow_nan=False,indent=2)
    print(json.dumps(dict(kind='summary',**{k:v for k,v in result.items() if k!='samples'})),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
