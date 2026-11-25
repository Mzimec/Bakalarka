"""Parallel whole-game self-play with a separate policy/value learner."""
import argparse
import json
from pathlib import Path


def _positive_count(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('count must be positive')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,help='Optional new JSONL output log; default: memory only')
    parser.add_argument('--actors',type=int,help='Override independent game processes')
    parser.add_argument('--games',type=_positive_count,
                        help='Total training games, overriding generations; evaluation is additional')
    parser.add_argument('--update-every',type=_positive_count,
                        help='Learn after this many games (1 = after each game); overrides games_per_generation')
    parser.add_argument('--checkpoint',type=Path,
                        help='Resume weights, Adam and game-stream progress; replay starts empty')
    parser.add_argument('--collect-only',action='store_true')
    parser.add_argument('--save-model',type=Path,
                        help='Explicitly save the final model/Adam snapshot to a new .npz file; never overwrite')
    args = parser.parse_args()
    if args.save_model is not None:
        if args.save_model.exists() or not args.save_model.parent.is_dir():
            parser.error('--save-model needs a new file in an existing directory')
        if args.output is not None and args.save_model.resolve() == args.output.resolve():
            parser.error('--save-model and --output must be different files')
    from game.ai.training.self_play_config import SelfPlayConfig
    from game.ai.training.self_play import run_self_play
    data = json.loads(args.config.read_text(encoding='utf-8'))
    if args.actors is not None:
        data['actors'] = args.actors
    if args.games is not None:
        data['total_games'] = args.games
    if args.update_every is not None:
        data['games_per_generation'] = args.update_every
    if args.collect_only:
        data['collect_only'] = True
    config = SelfPlayConfig.parse(data)
    result = run_self_play(config,args.output,checkpoint=args.checkpoint)
    if args.save_model is not None:
        with args.save_model.open('xb') as stream:
            stream.write(result['model_snapshot'])
    print(f"{result['status']}: optimizer step {result['optimizer_step']}, revision {result['model_revision']}")
    return 0 if result['status'] != 'no_training_data' and result['source_unchanged'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
