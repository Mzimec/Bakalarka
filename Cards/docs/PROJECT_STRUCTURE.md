# Project layout

Run commands and tests from `Cards/`. Public entry points remain unchanged:
`python -m game.bin.play_ai`, `python -m game.bin.ai_matches`,
`python -m game.bin.match_report`, and `python -m game.bin.starter_game`.

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
    ai/              Player strategy and automatic payment controller
    simulation/      Match and tournament orchestration
    reporting/       Human-readable match report rendering
    paths.py         Shared data and artifact paths
  helper/            Generic collections, queries and change tracking
  tests/             Engine and integration regression tests
  data/
    decks/arena_anb/  The five counted decklists
    *.json           Offline card characteristics and rules text
  docs/              Rules coverage, guides, testing history and specification
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

See [ENGINE_ROADMAP.md](ENGINE_ROADMAP.md) for staged rule work and removed legacy interfaces.

