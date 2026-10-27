"""Synchronous AlphaZero-style actors -> in-memory replay -> learner.

Only whole games cross process boundaries. There is no nested MCTS pool. Each
generation pins one immutable model; results are ingested in episode order.
The existing information-set search and fixed rollout opponent are preserved.
"""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from collections import Counter, deque
from dataclasses import asdict
from hashlib import sha256
from io import StringIO
import json
import multiprocessing
import os
from pathlib import Path
from random import Random
from time import perf_counter

from game.ai.self_play_config import SelfPlayConfig
from game.ai.self_play_data import EpisodeRecorder, ReplayBuffer, TrainingExample


class _ConsoleTail:
    """!
    @brief Bound console diagnostics without spooling a large game to disk.
    """
    def __init__(self, limit=16384):
        self.limit, self.text = limit, ''

    def write(self, value):
        """!
        @brief Append text to the bounded actor console tail held in RAM.
        """
        self.text = (self.text + value)[-self.limit:]
        return len(value)

    def flush(self):
        """!
        @brief Satisfy the stream interface without writing actor output to disk.
        """
        pass


def actor_init():
    # Spawn imports this module without importing NumPy. Set limits before the
    # optional model import, avoiding actors x BLAS threads oversubscription.
    """!
    @brief Initialize independent self-play workers without runtime output files.
    """
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'


_actor_models = {}


def play_episode(job):
    """!
    @brief Play an isolated self-play game and return examples and diagnostics.
    """
    from game.ai.training_model import PolicyValueModel, NeuralPrior, NeuralValue
    from game.ai.self_play_search import SelfPlaySearch
    from game.ai.game_origin import GameOrigin
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    from game.simulation.search_config import make_search_controller
    from game.simulation.match_runner import run_match
    from game.ai.simple_agent import SimpleAgent
    from contextlib import redirect_stdout, redirect_stderr
    started = perf_counter()
    config = SelfPlayConfig.parse(job['config'])
    neural = job['evaluation'] or job['generation'] >= config.bootstrap_generations
    model = None
    if neural:
        revision = job['revision']
        if revision not in _actor_models:
            _actor_models.clear()  # Bounded cache: old versions cannot accumulate.
            _actor_models[revision] = PolicyValueModel.from_bytes(job['model_snapshot'])
            settings = config.mcts
            _actor_models[revision].configure_cache(settings.get('neural_cache_size',256),settings.get('neural_cache_bytes',32*1024*1024))
        model = _actor_models[revision]
        if model.revision != job['revision']:
            raise ValueError('Actor checkpoint revision mismatch')
    policy_revision = job['revision'] if neural else 'heuristic-bootstrap-v1'
    decks = tuple(load_arena_starter(d) for d in job['decks'])
    origin = GameOrigin(decks,game_catalog,names=('Actor0','Actor1'),starting_player_idx=job['starting'])
    controllers, recorders = [], []
    for player in range(2):
        if job['evaluation'] and player != job['candidate_seat']:
            controllers.append(SimpleAgent(max_decisions=config.max_decisions))
            continue
        settings = dict(config.mcts,search_seed=job['search_seeds'][player])
        controller = make_search_controller(settings,origin,max_decisions=config.max_decisions)
        if neural:
            if controller.search.selection != 'uct':
                controller.search.action_prior = NeuralPrior(model)
            controller.search.evaluator = NeuralValue(model)
            controller.search.invalidate_model(job['revision'])
        if not job['evaluation']:
            controller.search = SelfPlaySearch(controller.search, rng=Random(job['noise_seeds'][player]),
                alpha=config.dirichlet_alpha,epsilon=config.dirichlet_epsilon)
            recorder = EpisodeRecorder(episode=job['episode'], player=player,generation=job['generation'],
                revision=policy_revision,rng=Random(job['behavior_seeds'][player]),temperature=config.temperature,
                temperature_drop_turn=config.temperature_drop_turn,validation_fraction=config.validation_fraction,
                max_training_actions=config.max_training_actions)
            if neural:
                recorder.encode = model.encode_information
            controller.search_choice, controller.on_search_decision = recorder.choose,recorder.observe
            recorders.append(recorder)
        controllers.append(controller)
    console = _ConsoleTail()
    match_log = StringIO() if config.log_matches else None
    with redirect_stdout(console), redirect_stderr(console):
        result = run_match(tuple(d.name for d in decks), None, log_stream=match_log,
            seed=job['seed'],starting_player=job['starting'],max_turns=config.max_turns,
            max_decisions=config.max_decisions,controllers=tuple(controllers),names=origin.names,
            decklists=decks,collect_decision_stats=False)
    counts, examples = Counter(), []
    for recorder in recorders:
        examples.extend(recorder.finish(result))
        counts.update(recorder.skipped)
    summary = dict(episode=job['episode'], generation=job['generation'], evaluation=job['evaluation'],
        seed=job['seed'],decks=job['decks'],starting=job['starting'],pid=os.getpid(),
        checkpoint_revision=job['revision'],policy_revision=policy_revision,
        candidate_seat=job['candidate_seat'],examples=len(examples),skipped=dict(counts),
        result=asdict(result),wall_seconds=perf_counter()-started,console_tail=console.text)
    if model is not None:
        summary['model_cache'] = model.cache.summary()
    if match_log is not None:
        summary['match_log'] = match_log.getvalue()
        match_log.close()
    if result.error or result.status in ('error','unsupported','aborted'):
        raise RuntimeError(f"Episode {job['episode']} failed: {result.status}: {result.error}; {console.text}")
    return dict(summary=summary, examples=tuple(examples))


def run_jobs(pool, jobs, limit, consume):
    """!
    @brief Ingest in submission order; pending + completed payloads stay bounded.

    A slow first game cannot cause unbounded finished examples to accumulate.
    The consumer releases training payloads immediately; only metadata remains.
    """
    iterator, pending, ready, results, peak = iter(jobs),{}, {}, [],0
    submitted, consumed = 0,0
    def fill():
        nonlocal peak, submitted
        while len(pending) + len(ready) < limit:
            job = next(iterator,None)
            if job is None:
                break
            pending[pool.submit(play_episode,job)] = submitted
            submitted += 1
        peak = max(peak,len(pending) + len(ready))
    try:
        fill()
        while pending:
            done,_ = wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                ready[pending.pop(future)] = future.result()
            # Futures also hold their payload; release them before refilling.
            done.clear()
            del future
            while consumed in ready:
                payload = ready.pop(consumed)
                consume(payload)
                results.append(payload['summary'])
                consumed += 1
                del payload
            fill()
    except BaseException:
        for future in pending:
            future.cancel()
        raise
    return results,peak


def _fingerprint():
    root = Path(__file__).resolve().parents[2]
    digest = sha256()
    for package in ('game','helper'):
        for path in sorted((root/package).rglob('*.py')):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _seed(master, domain, token):
    """!
    @brief Independent reproducible streams; no search receives the live deal seed.
    """
    return int.from_bytes(sha256(json.dumps((master,domain,token)).encode()).digest()[:16],'big')


def run_self_play(config, output=None, *, checkpoint=None, model=None):
    """!
    @brief Train in RAM, optionally writing one explicitly requested JSONL log.

    The returned model, immutable snapshot, replay and validation rows remain
    owned by the caller. Existing checkpoints can be read for a warm start.
    No checkpoints, shards, directories, caches or temporary files are created.
    """
    from game.ai.training_model import PolicyValueModel
    if not isinstance(config,SelfPlayConfig):
        config = SelfPlayConfig.parse(config)
    if config.log_matches and output is None:
        raise ValueError('log_matches requires an explicit JSONL output log')
    if checkpoint is not None and model is not None:
        raise ValueError('Choose a memory model or a read-only checkpoint, not both')
    if model is not None and not isinstance(model,PolicyValueModel):
        raise TypeError('model must be a PolicyValueModel')
    started, stamp = perf_counter(),_fingerprint()
    if model is None:
        model = PolicyValueModel.load(checkpoint) if checkpoint is not None else PolicyValueModel(hidden=config.hidden,seed=config.seed)
    initial_step = model.step
    semantic_config = {k:v for k,v in config.describe().items() if k not in ('actors','queued_games_per_actor','log_matches')}
    experiment = sha256((json.dumps(semantic_config,sort_keys=True)+model.revision+stamp).encode()).hexdigest()[:16]
    replay = ReplayBuffer(config.replay_capacity)
    validation = deque(maxlen=config.replay_capacity)
    rng, summaries = Random(config.seed ^ 0x1EA),[]
    snapshot = model.to_bytes()
    # The coordinator is the only file owner. No implicit parent directories.
    log = Path(output).open('x',encoding='utf-8') if output is not None else None
    def emit(kind, **data):
        if log is not None:
            log.write(json.dumps(dict(kind=kind,**data),allow_nan=False,ensure_ascii=False)+'\n')
            log.flush()
    def jobs(generation, evaluation=False):
        count = config.evaluation_games if evaluation else config.games_per_generation
        for index in range(count):
            token = f"{'eval' if evaluation else 'play'}-{index:06d}"
            episode = f"{experiment}-g{generation:04d}-{token}"
            # Evaluation seeds/deck/seat schedule stay fixed across checkpoints.
            stream_token = token if evaluation else f'{generation}-{token}'
            seed = _seed(config.seed,'match',stream_token)
            # During evaluation keep a deck pair fixed while swapping candidate
            # seats, then starting player. Swapping pair and seat together would
            # accidentally evaluate the candidate on only one deck/color.
            deck_index = index//4 if evaluation else index
            yield dict(config=config.describe(),episode=episode,generation=generation,evaluation=evaluation,
                model_snapshot=snapshot,revision=model.revision,seed=seed,
                search_seeds=tuple(_seed(config.seed,f'search-{p}',stream_token) for p in range(2)),
                noise_seeds=tuple(_seed(config.seed,f'noise-{p}',stream_token) for p in range(2)),
                behavior_seeds=tuple(_seed(config.seed,f'behavior-{p}',stream_token) for p in range(2)),
                decks=(config.decks[deck_index%len(config.decks)],config.decks[(deck_index+1)%len(config.decks)]),
                starting=(index//2)%2,candidate_seat=index%2)
    try:
        emit('configuration',config=config.describe(),source_fingerprint=stamp,experiment_id=experiment)
        with ProcessPoolExecutor(max_workers=config.actors,mp_context=multiprocessing.get_context('spawn'),
                                 initializer=actor_init) as pool:
            for generation in range(config.generations):
                phase_started = perf_counter()
                accepted, held_out = 0,0
                def consume(payload):
                    nonlocal accepted, held_out
                    episode = payload['summary']
                    if len(payload['examples']) != episode['examples']:
                        raise ValueError('Actor example count mismatch')
                    for row in payload['examples']:
                        if not isinstance(row,TrainingExample):
                            raise TypeError('Invalid actor example')
                        if row.episode != episode['episode'] or row.revision != episode['policy_revision']:
                            raise ValueError('Actor example provenance mismatch')
                        status, winner = episode['result']['status'],episode['result']['winner_index']
                        expected = 0. if status == 'draw' else (1. if row.player == winner else -1.)
                        if status not in ('win','draw') or row.outcome != expected or row.generation != generation:
                            raise ValueError('Actor terminal target does not match episode outcome')
                        if row.split == 'train':
                            replay.add(row)
                            accepted += 1
                        else:
                            validation.append(row)
                            held_out += 1
                    emit('episode',**episode)
                    # Optional full match records go only to the output log;
                    # no unbounded duplicate archive in generation metadata.
                    episode.pop('match_log',None)
                results, peak = run_jobs(pool,jobs(generation),config.actors*config.queued_games_per_actor,consume)
                collection_seconds = perf_counter()-phase_started
                losses = []
                learning_started = perf_counter()
                if not config.collect_only and len(replay.rows) >= config.batch_size:
                    for _ in range(config.updates_per_generation):
                        losses.append(model.train_batch(replay.sample(config.batch_size,rng),learning_rate=config.learning_rate,
                            weight_decay=config.weight_decay,value_weight=config.value_weight,clip_norm=config.clip_norm))
                snapshot = model.to_bytes()
                def consume_evaluation(payload):
                    if payload['examples']:
                        raise ValueError('Evaluation cannot enter training replay')
                    emit('evaluation_episode',**payload['summary'])
                    payload['summary'].pop('match_log',None)
                evaluation, _ = run_jobs(pool,jobs(generation,True),config.actors*config.queued_games_per_actor,consume_evaluation)
                labelled_eval = [r for r in evaluation if r['result']['status'] in ('win','draw')]
                wins = sum(r['result']['status']=='win' and r['result']['winner_index']==r['candidate_seat'] for r in labelled_eval)
                draws = sum(r['result']['status']=='draw' for r in labelled_eval)
                heldout_loss = None
                if validation:
                    rows = tuple(validation)
                    totals = Counter()
                    for start in range(0,len(rows),config.batch_size):
                        batch = rows[start:start+config.batch_size]
                        metrics,_ = model.loss_gradients(batch,weight_decay=0,value_weight=config.value_weight)
                        totals.update({k:v*len(batch) for k,v in metrics.items()})
                    heldout_loss = {k:v/len(rows) for k,v in totals.items()}
                summary = dict(generation=generation,collection_seconds=collection_seconds,
                    learning_and_evaluation_seconds=perf_counter()-learning_started,peak_inflight_games=peak,
                    accepted_training_examples=accepted,heldout_examples=held_out,replay_size=len(replay.rows),
                    replay_total_seen=replay.total_seen,model_revision=model.revision,optimizer_step=model.step,
                    losses=losses,heldout_loss=heldout_loss,episodes=results,evaluation=evaluation,
                    evaluation_terminal_games=len(labelled_eval),evaluation_wins=wins,evaluation_draws=draws)
                summaries.append(summary)
                emit('generation',**summary)
                print(f"Generation {generation}: {len(results)} games, {accepted} training examples, "
                      f"{held_out} held out, optimizer step {model.step}",flush=True)
        result = dict(status='collected' if config.collect_only else ('trained' if model.step>initial_step else 'no_training_data'),
            wall_seconds=perf_counter()-started,source_fingerprint=stamp,source_unchanged=stamp==_fingerprint(),
            generations=summaries,optimizer_step=model.step,optimizer_steps_added=model.step-initial_step,
            experiment_id=experiment,model_revision=model.revision)
        emit('summary',**result)
        return dict(result,model=model,model_snapshot=snapshot,replay=replay,validation=validation)
    except BaseException as error:
        emit('failure',error=type(error).__name__+': '+str(error),source_fingerprint=stamp,
             source_unchanged=stamp==_fingerprint())
        raise
    finally:
        if log is not None:
            log.close()
