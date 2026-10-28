"""Replay logged live choices to capture exact roots without repeating old searches.

All captures stay in RAM. Each measured search starts with a detached planner;
the replayed game's earlier search statistics are deliberately not reconstructed.
"""
import argparse
from contextlib import redirect_stdout
from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from benchmarks.mcts_decision_latency import Captured, Components, detached_planner


def capture(log_path, indices):
    """! @brief Regenerate logged player positions and verify their full information keys. """
    if not indices or any(type(index) is not int or index < 1 for index in indices):
        raise ValueError('Select at least one positive search index')
    from game.ai.game_origin import GameOrigin
    from game.ai.information_set_search import InformationSetSearch, InformationSearchResult, ActionEstimate
    from game.ai.search_actions import PlayerAction
    from game.ai.information_set_search import InformationSearchMetrics
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    from game.simulation.config import make_controller
    from game.simulation.match_runner import run_match

    configuration, rows, starts, rule_order = None, [], {}, {}
    with Path(log_path).open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['kind'] == 'diagnostic_configuration':
                configuration = row
            elif row['kind'] == 'search_start':
                starts[row['search_index']] = row
            elif row['kind'] == 'mcts_request' and row['detail'].get('search'):
                rows.append(row)
            if row['kind'] == 'search_start':
                for zone in ('hand', 'battlefield', 'graveyard', 'exile', 'stack'):
                    for card in row['view'][zone]:
                        if card.get('identity_visible'):
                            rule_order.setdefault(card['name'], {
                                field: [a['key'] for a in card[field]] for field in ('abilities', 'triggers')})
    if configuration is None or max(indices) not in starts:
        raise ValueError('The diagnostic log does not contain the requested searches')
    if configuration.get('opponent') != 'modular' or configuration.get('starting_player') != 1:
        raise ValueError('This replay adapter supports the logged Modular duel with starting player 1')
    captures = {}
    cursor = 0

    def recorded(planner, factory, information, **kwargs):
        nonlocal cursor
        row = rows[cursor]
        cursor += 1
        assert row['search_index'] == cursor
        expected = starts[cursor]
        if information.key != expected['information_key']:
            actual = information.current.observation()
            def difference(a, b, path='view'):
                if type(a) is not type(b):
                    return path, type(a).__name__, type(b).__name__
                if isinstance(a, dict):
                    for k in a:
                        if a[k] != b.get(k):
                            return difference(a[k], b.get(k), f'{path}.{k}')
                elif isinstance(a, list) and len(a) == len(b):
                    for i, (left, right) in enumerate(zip(a, b)):
                        if left != right:
                            return difference(left, right, f'{path}[{i}]')
                return path, a, b
            raise AssertionError(f'Root {cursor} diverged: {difference(expected["view"], actual)}')
        if cursor in indices:
            captures[cursor] = detached_planner(planner), factory, information, kwargs
        if cursor >= max(indices):
            raise Captured()
        result = dict(row['detail']['search'])
        result['choice'] = PlayerAction(**result['choice'])
        result['estimates'] = tuple(ActionEstimate(**dict(e, action=PlayerAction(**e['action'])))
                                    for e in result['estimates'])
        result['metrics'] = InformationSearchMetrics(**result['metrics'])
        return InformationSearchResult(**result)

    decks = tuple(load_arena_starter(c) for c in configuration['colors'])
    def ordered_catalog():
        # Authored definition sets can enumerate differently across processes.
        # Restore their logged presentation order; no rule or option is removed.
        catalog = game_catalog()
        for name, fields in rule_order.items():
            if name not in catalog:
                continue
            definition = catalog[name]
            changes = {}
            for field, order in fields.items():
                values = getattr(definition, field)
                values = values.values() if hasattr(values, 'values') else values
                ranks = {key: i for i, key in enumerate(order)}
                changes[field] = tuple(sorted(values, key=lambda a: (ranks.get(a.key, len(ranks)), a.key)))
            catalog[name] = replace(definition, **changes)
        return catalog

    origin = GameOrigin(decks, ordered_catalog, names=('MCTS', 'Modular'), starting_player_idx=1)
    seed = configuration['seed']
    agents = (make_controller(dict(type='mcts', mcts=configuration['settings']), seed=seed,
                  max_decisions=10000, search_origin=origin),
              make_controller(dict(type='modular'), seed=seed+1, max_decisions=10000))
    with patch.object(InformationSetSearch, 'search', recorded), redirect_stdout(io.StringIO()), \
            patch('game.simulation.match_runner.game_catalog', ordered_catalog):
        try:
            result = run_match(tuple(configuration['colors']), seed=seed, starting_player=1,
                      controllers=agents, decklists=decks, names=('MCTS', 'Modular'),
                      max_turns=100, max_decisions=10000)
            raise AssertionError(f'Replay ended before root {max(indices)}: {result.status}: {result.error}')
        except Captured:
            pass
    assert set(captures) == set(indices)
    return captures


def measure(captures):
    """! @brief Measure unchanged work, answer and exclusive components at captured roots. """
    from game.ai.information_set_search import InformationSetSearch
    for index, (planner, factory, info, kwargs) in captures.items():
        components = Components()
        started = perf_counter()
        with components.installed():
            result = InformationSetSearch.search(detached_planner(planner), factory, info, **kwargs)
        seconds = perf_counter() - started
        facts = asdict(result)
        metrics = facts.pop('metrics')
        # Timing and cache diagnostics are not algorithmic work or answers.
        work = {k: v for k, v in metrics.items() if not any(s in k for s in
                ('_ms', 'cache', 'bytes', 'timing'))}
        signature = hashlib.sha256(json.dumps(dict(answer=facts, work=work), sort_keys=True).encode()).hexdigest()
        print(json.dumps(dict(root=index, information_key=info.key, seconds=seconds,
              signature=signature, result=facts, metrics=metrics, **components.summary())), flush=True)


def main():
    """! @brief Read an existing diagnostic log; emit measurements only to stdout. """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, default=Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl')
    parser.add_argument('--roots', type=int, nargs='+', default=[1, 87, 104])
    args = parser.parse_args()
    measure(capture(args.log, args.roots))


if __name__ == '__main__':
    main()
