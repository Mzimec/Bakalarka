"""Parallel whole-game self-play with a separate policy/value learner."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,help='Optional new JSONL output log; default: memory only')
    parser.add_argument('--actors',type=int,help='Override independent game processes')
    parser.add_argument('--checkpoint',type=Path,help='Warm start weights and Adam state; starts a new replay/run')
    parser.add_argument('--collect-only',action='store_true')
    args = parser.parse_args()
    from game.ai.self_play_config import SelfPlayConfig
    from game.ai.self_play import run_self_play
    data = json.loads(args.config.read_text(encoding='utf-8'))
    if args.actors is not None:
        data['actors'] = args.actors
    if args.collect_only:
        data['collect_only'] = True
    config = SelfPlayConfig.parse(data)
    result = run_self_play(config,args.output,checkpoint=args.checkpoint)
    print(f"{result['status']}: optimizer step {result['optimizer_step']}, revision {result['model_revision']}")
    return 0 if result['status'] != 'no_training_data' and result['source_unchanged'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
