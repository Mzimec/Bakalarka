# Card engine

Project map: [structure and moved modules](docs/PROJECT_STRUCTURE.md).
Code is grouped under `game/`; guides are in `docs/`; decklists and card data
are in `data/`; match logs and test reports are in `artifacts/`.


Python 3.12+ is required. The engine implements a tested two-player rules core;
it is **not a complete implementation of every Magic mechanic or every card**.
See [rules coverage and design review](docs/ENGINE_RULES.md) and [test report](docs/TESTING.md).
For continuous-effect layers, declared dependencies and replacement definitions,
see [architecture and examples](docs/LAYERS_AND_REPLACEMENTS.md).

## Run

All five ANB starter colors can play automated matches using the real engine:

```powershell
cd Cards
..\.venv\Scripts\python.exe -B -m game.bin.ai_matches --output artifacts/runs/starter-example --seed 2026
```

This runs 20 matches and writes individual JSONL logs plus readable and JSON
summaries. See [AI matches, log format and code style](docs/STARTER_AI.md).
In the interactive console, `inspect <id>` now includes card types, subtypes
and mana cost.

For the white/red Arena starter decks with automatic mana payment and automatic
passing of quiet priority windows, run from `Cards/`:

```powershell
..\.venv\Scripts\python.exe -B -m game.bin.starter_game
```

Use `play` for guided choices and a source preview before confirming, or
`play <card-id> [target]` directly. `autopass off` restores manual priority.
See [automatic mana and priority controls](docs/AUTO_MANA_AND_PRIORITY.md) for scope,
examples and architecture notes.

From the repository root:

```powershell
cd Cards
..\.venv\Scripts\python.exe -B -m game.bin.dummy_game
```

The expanded teaching demo uses the full turn sequence, combat, lands, mana,
triggers and cleanup. Alice and Bob share the terminal. It starts with an
intentionally large hand of examples and 10 life, not tournament game setup.
Most teaching spells cost zero, including the simplified example called
Lightning Bolt. These definitions are not an Oracle card database.
Ember Adept and Watchful Adept explicitly have haste.

The expanded hand also contains `marshal1` (planeswalker creating Soldier tokens),
`blade1` (equipment, equip `{1}`), `aura1` (+1/+1 Aura) and `scout1` (infect).
See [permanent mechanics and examples](docs/PERMANENTS.md). Status shows loyalty,
tokens, attachments and poison. Attack a planeswalker with `attack c8:c20`,
using the IDs displayed in your game.

At the first upkeep, both players pass to reach the first main phase. The starting
player skips the entire first draw step. Example main-phase commands:

```text
play mountain1
activate mountain1
play stone1
pass
pass
activate stone1
play blast1 bob
pass
pass
```

`play <land>` plays a land without using the stack. By default, one land may be
played per turn, during your main phase with priority and an empty stack.
`activate <land>` taps it for its intrinsic mana ability, without the stack.
Casting can automatically tap supported mana sources; manual activation lets you
choose sources yourself. Unused mana empties at every step/phase boundary.

`play` or `activate` alone opens the incremental builder. `options`, `back`,
`cancel`, and `confirm` let you inspect/change choices without paying costs.
Direct spells can choose a structural mode (`mode=2`) or mana X (`x=3`).
Multiple targets in one slot are comma-separated; `-` is an optional empty group.

Combat has separate declaration prompts:

```text
attack c8 c12 bob
block c20:c8 c21:c12
pass
```

An entire declaration is checked before any creature taps. Ordinary priority
opens after declarations. `pass` at a declaration selects no attackers/blockers;
`pass` at a priority prompt passes priority. Card IDs are global and stable;
use `hand`, `status`, `inspect <id>`, `help`, `concede` or `quit` as needed.
Cleanup asks the active human player to select discards down to seven cards.

For a normal-sized, seeded game use the Arena Beginner starter demo:

```powershell
..\.venv\Scripts\python.exe -B -m game.bin.starter_game
```

It starts Keep the Peace (white) versus Goblins Everywhere (red), with 20 life,
60-card decks, seven-card opening hands and London mulligans. The cards in this
matchup have their printed mana costs and the builder automatically plans and
activates basic lands while paying a spell or ability cost. The complete
reference lists for all five one-colour Arena Beginner decks are in
`decks/arena_anb`; the executable card catalog currently covers the white/red
vertical slice.

## Engine API

`State(players)` and `GameLoop(None, ActionProcessor(ResolutionEngine(
OperationExecutor(), EventBus())))` compose the real engine. All players must
belong to that state; use `Player.add_card` for setup. The default state uses
the normal turn sequence and first-player draw skip. `create_demo_game()` retains
the small fixture; `create_demo_game(expanded=True)` enables all examples and the
full turn. Set `full_rules=True` for the full sequence with the small fixture.

`CardDefinition(mana_cost="{2}{R}", ...)` supplies the printed casting cost.
Supported syntax includes colored, generic, explicit colorless, hybrid,
monocolored hybrid, Phyrexian and X symbols. An omitted prototype cost defaults
to `{0}` for compatibility with teaching cards; explicit `None` means no mana
cost and cannot be paid to cast a spell. Ability costs can still be declared
using existing `ManaActionNode` or `SubAbilityDefinition.mana_cost` objects;
additional costs add to the printed card cost.

Rules belong in shared card/ability/effect definitions. Operations implement
state changes; use the resolution engine to execute them and collect events.
Direct `Player.move_card`/field changes are low-level setup/editor APIs: they
maintain state/indexes but do not themselves generate gameplay events.

`create_starter_demo_game()` is the ready-to-run integration entry point. Pass
`colors=("white", "red")`, a seed and optional controllers/configuration to
build the same game without the console. `State.get_mana_sources()` and
`SourceActivatingManaSolver` keep payment planning side-effect free; the
resulting mana abilities are executed inside the cost transaction, so a failed
payment rolls back land taps and floating mana.

Controllers implement `get_action(state, player)`, optionally `choose_attackers`,
`choose_blockers`, `choose_discards`, `choose_legend`, `order_triggers`, `choose_trigger_action`,
`choose_replacement`, `accept_replacement`, `order_replacement_events`,
and `assign_combat_damage`. Default scripted choices are deterministic.
`ConsoleDecisionMaker` and `HumanDecisionMaker` share the maintained console.
`ActionBuilderSession(state, player).run()` remains a public compatibility entry.
Removed private classes from the old human-input prototype are not supported.

Queries still use the existing indexed registers and bitsets. Zone changes keep
command IDs stable while incrementing `zone_revision`; a target that leaves and
returns is a new game object for a previously selected spell. Query effects that
do not target can affect shroud/hexproof permanents normally.

## Tests

From `Cards/`:

```powershell
..\.venv\Scripts\python.exe -B -m pytest -q
..\.venv\Scripts\python.exe -B -m pytest -q --junitxml=artifacts/test-results/engine.xml
```

The whole suite must pass; no legacy test files are excluded. See
[TESTING.md](docs/TESTING.md) for the baseline, coverage map and review findings.

## Game setup and cost transactions

`game.game_loop.setup.create_game` prepares a two-player game from counted decklists,
with seeded shuffling, starting-player choice, seven-card hands and London mulligans.
`game.cards.decks.load_arena_starter(color)` reads one of five pinned ANB reference lists.
The white/red lists resolve through `game.cards.starter_cards.starter_catalog()`;
the remaining three lists intentionally fail fast until their card rules are
implemented.
See [setup, costs and next implementation proposals](docs/SETUP_AND_COSTS.md) for API
examples, supported rollback boundaries, and mana/variable design options.

Play against AI: `python -m game.bin.play_ai --deck white --opponent green`.
Each new match includes a readable `.txt` report next to its JSONL log.
See [STARTER_AI.md](docs/STARTER_AI.md) for controls and historical log conversion.


## Project navigation

See [the project layout](docs/PROJECT_STRUCTURE.md) for package responsibilities
and moved Python modules. [Playing against AI](docs/STARTER_AI.md),
[rules coverage](docs/ENGINE_RULES.md) and [test revisions](docs/TESTING.md)
are in `docs/`. Reference decks are in `data/decks/`; generated matches and
reports are in `artifacts/`. CLI commands in `game.bin` are unchanged.
