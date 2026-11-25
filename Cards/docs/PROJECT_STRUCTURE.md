# Project layout

Run commands and tests from `Cards/`. Public entry points remain unchanged:
`python -m game.bin.play`, `python -m game.bin.match`,
`python -m game.bin.tournament`, and `python -m game.bin.self_play`.

```text
Cards/
  game/
    bin/             Command-line entry points
    cards/           Deck loading, card catalogs and descriptive rules text
    console/         Console controller, commands and interactive choices
    rules/           Combat, lands and permanent mechanics
    game_state/      State, characteristics, layers and registers
    game_actions/    Ability generation, costs, execution and resolution
    game_loop/       Turn loop, setup and minimal runnable game
    mana/            Mana representation, source discovery and planning
    ai/              Player information, policies, sampled worlds and MCTS
      agents/        Live controllers and decision interface
      decisions/     Legal action descriptors and candidate generation
      information/   Visible views, player memory and incremental identities
      evaluation/    Values, policies, encoders and evaluation caches
      search/        Shared contracts, IS-MCTS/PIMC, trees and trajectories
      simulation/    Native replay, copies, workers and checkpoints
      sampling/      World recipes, hidden-card evidence and providers
      training/      Explicit self-play and model training
      experiments/   Disabled opt-in prototypes
      reference/     Tactical/scenario regression references
      composition.py AI object-graph builder
    simulation/      Match and tournament orchestration
    reporting/       Human-readable match report rendering
    paths.py         Shared data and artifact paths
  helper/            Generic collections, queries and change tracking
  tests/             Engine and integration regression tests
  data/
    decks/arena_anb/  The five counted decklists
    *.json           Offline card characteristics and rules text
  docs/              Current guides and specifications
    experiments/mcts/ Dated measurements and development reports
    generated/       Large generated diagnostic tables
  benchmarks/        Maintained profiling and regression tools
  configs/           Match, search and self-play configurations
  runs/              Retained historical numeric results and output logs
  artifacts/
    test-results/    Retained test reports and reference matches
    runs/            Local interactive match output
```

Foundational `enums.py`, `constants.py` and `stat_type.py` stay directly in
`game/`. Existing structured engine packages are retained. Card behavior and
algorithms are unchanged; imports and paths follow the new package ownership.
`game/bin/` remains stable for command-line callers. Python consumers should
use the new module paths listed below; old flat module paths were removed.

## Module moves

- `game.decks` → `game.cards.decks`
- `game.demo_cards` → `game.cards.demo_cards`
- `game.starter_cards` → `game.cards.starter_cards`
- `game.starter_extensions` → `game.cards.starter_extensions`
- `game.starter_support` → `game.cards.starter_support`
- `game.rules_text` → `game.cards.rules_text`
- `game.demo_game` → `game.console.demo_game`
- `game.console_commands` → `game.console.console_commands`
- `game.combat_commands` → `game.console.combat_commands`
- `game.command_choices` → `game.console.command_choices`
- `game.command_session` → `game.console.command_session`
- `game.priority_choices` → `game.console.priority_choices`
- `game.human_input` → `game.console.human_input`
- `game.combat` → `game.rules.combat`
- `game.lands` → `game.rules.lands`
- `game.permanents` → `game.rules.permanents`
- `game.setup` → `game.game_loop.setup`
- `game.minimal_game` → `game.game_loop.minimal_game`
- `game.filtering` → `game.game_state.filtering`
- `game.ai.match_runner` → `game.simulation.match_runner`
- `game.ai.readable_log` → `game.reporting.readable_log`

## Engine development

The AI source layout was organized on 2026-11-10. For every module's owner,
classes and importing callers, see the [AI map](../game/ai/README.md) and
[static inventory](generated/AI_MODULES_20261110.md). Python imports now use
the owner package, for example `game.ai.information.runtime_digest`.
Search JSON settings and the `game.bin` entry points remain unchanged.

See [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md) for staged work and
[AI_SEARCH.md](AI_SEARCH.md) for current search ownership and interfaces.
Mana planning is imported from `game.mana.mana_solver`.
Documentation belongs under `docs`; `game/ai/README.md` and `benchmarks/README.md`
provide local navigation into their source areas. See the
[documentation index](README.md) and [AI module map](../game/ai/README.md).
Dependency environments are excluded from source cleanup. Historical Python snapshots and one-shot scripts under
`runs` were removed; retained results are data rather than runtime dependencies.

