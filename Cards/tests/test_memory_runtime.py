"""No runtime disk transport, optional logs, bounded ordered actor ingestion."""
from concurrent.futures import Future
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import sys

import pytest

from runtime_write_guard import deny_writes, guarded_process_pool, guarded_search_job
from game.simulation.match_runner import run_match, run_tournament, TournamentPlayer
from game.cards.decks import load_arena_starter


def test_guard_detects_temporary_files_and_directory_mutations(tmp_path):
    import tempfile
    with deny_writes():
        with pytest.raises(AssertionError,match='write'):
            (tmp_path/'forbidden').write_text('x')
        with pytest.raises(AssertionError,match='mutation'):
            (tmp_path/'folder').mkdir()
        with pytest.raises(AssertionError):
            tempfile.TemporaryFile(dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_match_and_tournament_defaults_never_write_and_can_log_in_memory():
    players = tuple(TournamentPlayer(c,load_arena_starter(c),dict(type='simple')) for c in ('red','white'))
    with deny_writes():
        result = run_match(('red','white'),seed=42,max_turns=1)
        stream = StringIO()
        logged = run_match(('red','white'),seed=42,max_turns=1,log_stream=stream)
        games = run_tournament(None,players,games=1,seed=42,max_turns=1)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert not stream.closed and rows[0]['kind']=='match' and rows[-1]['kind']=='result'
    assert any(r['kind']=='state' for r in rows) and any(r['kind']=='event' for r in rows)
    assert result.status == logged.status == 'turn_limit' and result.log is logged.log is None
    assert len(games)==2 and all(r.log is None for r in games)


def test_cli_commands_and_config_have_no_implicit_log_destination(monkeypatch):
    from game.bin import match, tournament, self_play
    from game.ai import self_play as training
    from game.simulation import config as config_module
    captured = []
    def play(colors,output,**kwargs):
        captured.append(output)
        return run_match(colors,None,max_turns=1,seed=42,decklists=kwargs['decklists'])
    monkeypatch.setattr(match,'run_match',play)
    monkeypatch.setattr(tournament,'run_tournament',lambda output,*a,**kw:captured.append(output) or [])
    def train(config,output,**kwargs):
        captured.append(output)
        return dict(status='collected',optimizer_step=0,model_revision='test',source_unchanged=True)
    monkeypatch.setattr(training,'run_self_play',train)
    data = dict(players=[dict(deck='red',agent=dict(type='simple')),dict(deck='white',agent=dict(type='simple'))],
                max_turns=1)
    # Read configuration from a memory fixture, with no file writes even in setup.
    original_read = Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda self,**kw: json.dumps(data) if self.name=='match.json' else
                        '{}' if self.name=='self.json' else original_read(self,**kw))
    monkeypatch.setattr(config_module,'run_match',play)
    with deny_writes():
        for module, argv in ((match,['match']), (tournament,['tournament']),
                             (self_play,['self_play','--config','self.json','--collect-only'])):
            monkeypatch.setattr(sys,'argv',argv)
            assert module.main() == 0
        config_module.run_config('match.json')
    assert captured == [None]*4


@pytest.mark.parametrize('selection',['uct','puct','hierarchical_puct'])
@pytest.mark.parametrize('backend',['thread','process'])
def test_parallel_mcts_has_no_disk_spool(selection,backend):
    assert guarded_search_job(selection,backend) == (True,12)


@pytest.mark.parametrize('with_log',[False,True])
def test_real_neural_spawn_actors_transfer_snapshots_and_examples_without_files(monkeypatch,tmp_path,with_log):
    pytest.importorskip('numpy')
    from game.ai import self_play
    from game.ai.self_play_config import SelfPlayConfig
    from game.ai.training_model import PolicyValueModel
    monkeypatch.setattr(self_play,'ProcessPoolExecutor',guarded_process_pool)
    config = SelfPlayConfig(actors=2,generations=2,games_per_generation=2,evaluation_games=1,
        max_turns=1,collect_only=True,batch_size=1,bootstrap_generations=0,hidden=4,
        temperature=0,dirichlet_epsilon=0,log_matches=with_log,
        mcts=dict(deck_knowledge='both_lists',selection='puct',prior='heuristic',
                  worlds=1,iterations=2,max_depth=1,max_extensions=16,max_advances=128))
    output = tmp_path/'output.jsonl' if with_log else None
    with deny_writes(*([output] if output else [])):
        result = self_play.run_self_play(config,output)
        restored = PolicyValueModel.from_bytes(result['model_snapshot'])
    assert result['model'].revision == restored.revision == result['model_revision']
    assert len(result['generations']) == 2 and not result['replay'].rows
    assert all(e['policy_revision']==result['model_revision'] and e['pid']!=os.getpid()
               for g in result['generations'] for e in g['episodes']+g['evaluation'])
    assert list(tmp_path.iterdir()) == ([output] if output else [])
    if output:
        logs = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
        assert logs[0]['kind']=='configuration' and logs[-1]['kind']=='summary'
        episodes = [r for r in logs if r['kind']=='episode']
        assert len(episodes)==4 and all('match_log' in e and e['examples']==0 for e in episodes)
        assert all('match_log' not in e for g in result['generations'] for e in g['episodes'])


def test_actor_queue_backpressure_counts_out_of_order_completed_payloads(monkeypatch):
    from game.ai import self_play
    class Pool:
        submitted = []
        def submit(self, function, job):
            future = Future()
            future.set_result(dict(summary=dict(episode=job['episode']),examples=(job['episode'],)))
            self.submitted.append(future)
            return future
    pool, received = Pool(),[]
    def wait_reverse(pending,**kwargs):
        # Finish the second game first; the first prevents submitting more.
        if len(received)==0 and len(pool.submitted)==2 and len(pending)==2:
            return {pool.submitted[1]},set(pending)-{pool.submitted[1]}
        assert len(pool.submitted) <= len(received)+2
        return {next(iter(pending))},set()
    monkeypatch.setattr(self_play,'wait',wait_reverse)
    results, peak = self_play.run_jobs(pool,(dict(episode=str(i)) for i in range(7)),2,
                                      lambda payload:received.extend(payload['examples']))
    assert received == [str(i) for i in range(7)]
    assert [r['episode'] for r in results] == received and peak==2


def test_learning_roundtrip_stays_in_memory_and_advances_model_generation(monkeypatch):
    pytest.importorskip('numpy')
    from game.ai import self_play
    from game.ai.self_play_config import SelfPlayConfig
    from game.ai.training_model import PolicyValueModel
    from test_self_play import example
    row = example()
    revisions = []
    def run_jobs(pool,jobs,limit,consume):
        results=[]
        for job in jobs:
            revisions.append(PolicyValueModel.from_bytes(job['model_snapshot']).revision)
            sample=replace(row,episode=job['episode'],revision=job['revision'],generation=job['generation'])
            summary=dict(episode=job['episode'],policy_revision=job['revision'],examples=1,
                         result=dict(status='win',winner_index=sample.player))
            consume(dict(summary=summary,examples=(sample,)))
            results.append(summary)
        return results,1
    monkeypatch.setattr(self_play,'run_jobs',run_jobs)
    config=SelfPlayConfig(actors=1,generations=2,games_per_generation=1,evaluation_games=0,
                         bootstrap_generations=0,batch_size=1,updates_per_generation=1,hidden=4)
    model=PolicyValueModel(hidden=4,seed=1)
    before=model.revision
    with deny_writes():
        result=self_play.run_self_play(config,model=model)
    assert result['model'] is model and result['optimizer_steps_added']==2 and len(result['replay'].rows)==2
    assert revisions[0]==before and revisions[1]!=before
    assert PolicyValueModel.from_bytes(result['model_snapshot']).step==2
