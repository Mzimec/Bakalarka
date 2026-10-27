"""Test-only filesystem audit guard, including actual spawned game workers."""
from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor
import os
import stat
import sys

_enabled = False
_allowed = frozenset()


def _path(value):
    return os.path.normcase(os.path.abspath(os.fsdecode(value))) if not isinstance(value,int) else value


def _audit(event, args):
    if not _enabled:
        return
    if event == 'open':
        path, mode, flags = args
        # Windows spawn wraps an already-open anonymous pipe with fdopen.
        # Permit actual pipes/console descriptors, never regular disk handles.
        if isinstance(path,int) and (stat.S_ISFIFO(os.fstat(path).st_mode) or path in (0,1,2)):
            return
        writing = (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (
            isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if writing and _path(path) not in _allowed:
            raise AssertionError(f'Runtime attempted filesystem write: {path!r}')
    elif event in ('os.mkdir','os.rename','os.remove','os.rmdir','os.link','os.symlink',
                   'os.truncate','tempfile.mkstemp','tempfile.mkdtemp'):
        raise AssertionError(f'Runtime attempted filesystem mutation: {event} {args!r}')


sys.addaudithook(_audit)


@contextmanager
def deny_writes(*allowed_logs):
    global _enabled, _allowed
    previous = _enabled,_allowed
    _allowed = frozenset(_path(p) for p in allowed_logs)
    _enabled = True
    try:
        yield
    finally:
        _enabled,_allowed = previous


def guarded_actor_init():
    global _enabled
    from game.ai.self_play import actor_init
    actor_init()
    _enabled = True


def guarded_process_pool(*args,**kwargs):
    kwargs['initializer'] = guarded_actor_init
    return ProcessPoolExecutor(*args,**kwargs)


def guarded_search_job(selection, backend):
    """Run real root parallel search under a guard in the coordinating process."""
    from test_parallel_search import factory, planner
    from game.ai.parallel_search import ParallelInformationSetSearch
    from unittest.mock import patch
    with patch('game.ai.parallel_search.ProcessPoolExecutor',guarded_process_pool), deny_writes():
        root = factory(0).start().player_information()
        with ParallelInformationSetSearch(planner(selection),workers=2,backend=backend) as search:
            result = search.search(factory,root,seed=42)
    return not result.used_fallback,result.metrics.iterations_completed


def training_probe():
    """Optional full-game verification, stdout only, no new artifact directory."""
    from unittest.mock import patch
    from game.ai.self_play import run_self_play
    from game.ai.self_play_config import SelfPlayConfig
    from game.ai.training_model import PolicyValueModel
    config = SelfPlayConfig(actors=2,generations=2,games_per_generation=2,evaluation_games=0,
        max_turns=100,batch_size=2,updates_per_generation=1,hidden=4,
        validation_fraction=0,temperature=0,dirichlet_epsilon=0,
        replay_capacity=256,mcts=dict(deck_knowledge='both_lists',selection='puct',prior='heuristic',
            worlds=1,iterations=2,max_depth=1,max_extensions=16,max_advances=512,
            compress_forced=True,require_root_coverage=False,force_initial_coverage=False))
    with patch('game.ai.self_play.ProcessPoolExecutor',guarded_process_pool), deny_writes():
        result=run_self_play(config)
        assert result['status']=='trained' and result['optimizer_steps_added']==2
        assert PolicyValueModel.from_bytes(result['model_snapshot']).revision==result['model_revision']
        assert result['source_unchanged'] and 0<len(result['replay'].rows)<=config.replay_capacity
        for generation in result['generations']:
            assert generation['accepted_training_examples']>0
            assert all(e['result']['status'] in ('win','draw') for e in generation['episodes'])
        return dict(status=result['status'],steps=result['optimizer_step'],replay=len(result['replay'].rows),
                    generations=[dict(generation=g['generation'],examples=g['accepted_training_examples'],
                                      revision=g['model_revision'],games=[(e['result']['status'],e['examples']) for e in g['episodes']])
                                 for g in result['generations']],wall_seconds=result['wall_seconds'])
