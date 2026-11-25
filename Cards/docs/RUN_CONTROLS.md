# Running the MtG Engine

## 1. Environment

Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

Run commands from the project root using:

```powershell
python -B -m game.bin.<command>
```

---

## 2. Available commands

### Tournament

```powershell
python -B -m game.bin.tournament
```

Runs repeated matches between two configured AI agents.

Typical arguments:

```text
--agent1
--deck1
--agent2
--deck2
--rounds
--seed
--max-turns
--max-decisions
--output
--log-matches
```

Example:

```powershell
python -B -m game.bin.tournament `
    --agent1 modular `
    --deck1 blue `
    --agent2 simple `
    --deck2 red `
    --rounds 100
```

For every tournament round, both starting positions are played.

---

### Match

```powershell
python -B -m game.bin.match
```

Runs one configurable AI-vs-AI match.

Typical arguments:

```text
--agent1
--deck1
--agent2
--deck2
--seed
--starting-player
--max-turns
--max-decisions
--output
```

Example:

```powershell
python -B -m game.bin.match `
    --agent1 modular `
    --deck1 blue `
    --agent2 simple `
    --deck2 red `
    --seed 123
```

To create a detailed match log:

```powershell
python -B -m game.bin.match `
    --output runs/match.jsonl
```

For `match.py`, `--output` is a file path, not a directory.

---

### Play

```powershell
python -B -m game.bin.play
```

Runs an interactive human-vs-AI game.

The intended setup is:

```text
Human
    vs
current primary AI
```

Typical arguments:

```text
--human-deck
--ai-deck
--seed
--starting-player
--max-turns
--max-decisions
--output
```

Example:

```powershell
python -B -m game.bin.play `
    --human-deck red `
    --ai-deck blue
```

---

### Human versus local Ollama

From the repository root in **cmd.exe**, with Ollama running and `qwen3:8b`
already downloaded:

```bat
set PYTHONPATH=Cards
.venv\Scripts\python.exe -B -m game.bin.play --ai-agent ollama --human-deck white --ai-deck red
```

You control white through console commands; local Qwen controls red. Choose
either deck from `white`, `blue`, `black`, `red`, `green`. Use
`--starting-player ai` to let Qwen start, or `--seed 2026` to fix the setup seed.
Enter `help` for commands, `pass` to pass priority, `inspect CARD` for details,
or `concede` to concede.

The [interactive Ollama settings](../configs/ollama_play.json) select
`qwen3:8b`, `think=false`, `num_gpu=37`, `num_ctx=4096`, a 60-second timeout and
up to 10,000 model requests. Override with `--llm-settings PATH` (a JSON object
of LLM settings, not a whole match config) or `--llm-model NAME`. Other models
may need different layer settings. This mode requires provider `ollama`.

The launcher checks the server/model before starting. It prints waiting status
for model choices and reports heuristic fallbacks immediately and at game end.
The existing modular agent still handles auxiliary decisions and mana payment.
No match logs are written unless `--output NEW_FILE.jsonl` is supplied.

Local Qwen configs now enable `compact_observation=true`. The LLM receives the
already visibility-filtered state with live card statistics, card rule text,
current abilities, target slots, nontrivial branch plans, stack, effects and
remembered hidden-card facts. Identical current rule descriptions share a
per-request `rule_catalog`; `rules_ref` resolves a card's current rules.
`card_defaults` and `ability_defaults` supply omitted default values. Native
single-effect targetless wrappers are simplified, including exact source-zone
movements. This presentation is not the full engine execution schema or an
MCTS identity, and never executes effects or reads hidden runtime objects.
Repeated currently visible identities/locations are omitted from memory;
uniform event records use an ordered `columns`/`rows` table without deleting
events. Unknown descriptions remain explicitly unknown.

Ollama replies are constrained by a JSON schema to exactly `{"choice": N}`
with `N` one of the offered candidate indices. The output budget is 128 tokens;
incomplete responses and token-limit termination are rejected before parsing.
No incomplete JSON is repaired into an action and there are no automatic
retries. The selector exposes `last_response` with completion reason and
returned prompt/output token counts for diagnosis, without retaining raw text.
Restart an already running game process after updating the adapter.

The compact projection reduces prompt size but is not a universal bound on
all MTG positions. Larger boards/complex custom descriptions may still exceed
the configured context; do not infer full-information reasoning merely from a
valid JSON reply. The short local checks are protocol checks, not a win-rate
evaluation.

## MCTS versus a local LLM benchmark

The default local model is `qwen3:8b` (about 5.2 GB of model files).
After installing Ollama, download it once:

```bat
ollama pull qwen3:8b
```

From the repository root in **cmd.exe**:

```bat
set PYTHONPATH=Cards
.venv\Scripts\python.exe -B -m benchmarks.mcts_vs_llm --mcts-deck white --llm-deck red
```

The default [benchmark config](../configs/mcts_vs_llm.json) references the current
`mcts_play_128_native.json` preset without overriding it: 128 trajectories total,
four sampled worlds, depth 8 and heuristic PUCT. Choose either deck from `white`,
`blue`, `black`, `red`, `green`. Use `--model MODEL_NAME` to override the model,
or change `llm.model` in the config. Local config sets `think=false` so the
short output budget is used for the JSON choice, rather than a reasoning trace.
The local Qwen preset also sets `num_gpu=37` (layers, not GPU count) and
`num_ctx=4096`. On the RTX 3060 Laptop 6 GB, automatic placement used 70% GPU;
explicit 37-layer placement was observed as 100% GPU at this context length.
A short JSON smoke request completed in 8.77 s including 8.27 s of model load;
this is not a whole-game or full-state performance result. Larger contexts or
other GPU workloads can require more memory. Other models may need different
layer settings. The 4K context is finite: full game prompts must be measured
before drawing conclusions about policy quality or lost input through truncation.
These settings affect the LLM policy and are not a claim about its playing strength.
Ollama must already be
running; the benchmark checks its installed model list before starting a game.
This config belongs to `benchmarks.mcts_vs_llm`, not `game.bin.match`.

For three pairs of games, with each seed played once with each starting player:

```bat
.venv\Scripts\python.exe -B -m benchmarks.mcts_vs_llm --model MODEL_NAME --mcts-deck white --llm-deck red --paired --rounds 3
```

The two agents keep their selected decks in a pair. Use the same deck for both
agents to reduce deck matchup differences. `--seed`, `--starting-player mcts|llm`,
`--max-turns` and `--max-decisions` override benchmark defaults; `--dry-run`
validates and prints the effective config without HTTP or games.

The benchmark prints progress, match results, decision timing, MCTS diagnostics
and LLM request/fallback counts to stdout. LLM timeouts, invalid responses and
exhausted request budgets fall back to the existing heuristic and are reported.
Turn/decision limits are incomplete games, not draws. Python process CPU time
excludes the separate Ollama server; wall time includes waiting for it. Small
samples and games with fallbacks do not establish comparative playing strength.

No output files are created by default. To save `summary.json` and each match's
JSONL/TXT logs, add a **new** output directory:

```bat
.venv\Scripts\python.exe -B -m benchmarks.mcts_vs_llm --model MODEL_NAME --mcts-deck white --llm-deck red --paired --output Cards/artifacts/runs/mcts-vs-llm-01
```

### MCTS versus OpenAI GPT-6.1 Sol

The same benchmark supports OpenAI Responses API with the separate
[OpenAI config](../configs/mcts_vs_openai.json). No Ollama server or additional
Python packages are required. From the repository root in **cmd.exe**:

```bat
set OPENAI_API_KEY=YOUR_API_KEY
set PYTHONPATH=Cards
.venv\Scripts\python.exe -B -m benchmarks.mcts_vs_llm --config Cards/configs/mcts_vs_openai.json --mcts-deck white --llm-deck red
```

The configured model ID is `gpt-6.1-sol`, reasoning effort `low`, timeout 120 s,
at most 1,000 requests per game and 2,048 output tokens per request (including
reasoning tokens). These are request/output limits, **not a monetary budget**.
API access and token billing are required. The key is read from
`OPENAI_API_KEY` at request time; only the environment variable name belongs in
the config. It is not saved in logs or sent in the game prompt.

Use `--model` to override the API model ID, or `--paired --rounds 3` for six
games. Other model families may require changing `reasoning_effort` (set it to
`null` to omit the parameter). An authenticated model-access GET runs before a
game; `--dry-run` skips HTTP and does not require a key. Actual choices use
strict JSON schema, validate the returned candidate index, send only the
existing actor-visible observation, and do not send previous conversation
history or attach tools. `store=false` is set on Responses requests; this is
not a claim of zero provider-side data retention.

Each decision makes at most one API request (no automatic retries). API errors,
refusals, incomplete responses and invalid choices use the same measured
heuristic fallback as Ollama. Per-game and aggregate reports include returned
input/output/total token usage, including incomplete responses when usage is
available; failed requests without returned usage cannot be accounted for by
this counter. No currency estimate or playing-strength claim is inferred.

Protocol/model references checked on 2026-11-24:
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[API quickstart](https://developers.openai.com/api/docs/quickstart).

# 3. Console controls

The interactive console accepts text commands.

Cards can usually be referenced either by their unique card ID or, when
unambiguous, by card name. Using the card ID is the safest option.

## Command aliases

The command parser supports these aliases:

```text
p        -> play
cast     -> play
a        -> activate
info     -> inspect
options  -> help
exit     -> quit
```

An empty command is interpreted as:

```text
pass
```

---

## Passing priority

Use:

```text
pass
```

An empty input also behaves as `pass`.

During combat declaration, `pass` means declaring no attackers or no blockers.

---

## Playing a card

Basic command:

```text
play <card>
```

Aliases:

```text
p <card>
cast <card>
```

Example:

```text
play white-3
```

The card may be referenced by ID or by an unambiguous name.

### Playing a land

When the selected card is a land, the console asks for confirmation.

Typical flow:

```text
play land white-3/confirm>
```

Available responses:

```text
confirm
back
cancel
```

Playing a land does not use the stack.

### Casting a spell

When the selected card is a spell, the console opens an incremental command
builder.

Depending on the spell, the builder may go through:

```text
card
ability
mode
targets
cost_mode
cost
confirm
```

Stages with only one legal choice may be selected automatically.

Nothing is paid or applied until the final:

```text
confirm
```

---

## Activating an ability

Use:

```text
activate <card>
```

Alias:

```text
a <card>
```

The console then opens the same incremental command builder used for spells.

Depending on the ability, the player may need to choose:

- an ability,
- a mode,
- targets,
- a cost mode,
- cost targets,
- final confirmation.

---

## Interactive command builder

While building a `play` or `activate` action, the following control commands are
available.

### Show current options

```text
options
?
help
```

These display legal choices for the current stage.

### Go back

```text
back
```

Returns to the previous manually selected stage and clears choices made after it.

### Cancel

```text
cancel
quit
```

Discards the action without executing it.

### Confirm

```text
confirm
```

Executes the fully built action.

Before confirmation, the console prints a summary containing information such as:

- selected card,
- selected ability,
- selected targets,
- cost targets,
- non-mana costs,
- mana sources that will be auto-tapped,
- final mana payment.

---

## Choosing a card

When the builder asks for a card, enter exactly one card reference.

Examples:

```text
white-3
```

or, if unambiguous:

```text
"Lightning Bolt"
```

Quoted names can be used for card names containing spaces.

If a name is ambiguous, the console requires a unique card ID.

---

## Choosing an ability

When multiple executable abilities are available, the console lists their keys.

Enter the displayed ability key.

Example:

```text
firebreathing
```

If the ability cannot currently be paid, targeted, or legally executed, it is not
offered as an executable option.

---

## Choosing a mode

For modal actions, legal modes are numbered.

Example output:

```text
1: Deal damage
2: Draw a card
```

Select a mode by entering its number:

```text
1
```

Modes that have no legal target configuration or payable cost are not accepted.

---

## Choosing targets

When an action requires targets, the console prints each target slot together
with its legal candidates.

Example conceptually:

```text
target-0: red-2 (Goblin), blue-4 (Merfolk)
  selector: ...
```

One target group must be entered for each target slot.

Targets within one group are separated by commas.

Example:

```text
red-2,red-3 blue-4
```

This represents two target groups:

```text
group 1 -> red-2, red-3
group 2 -> blue-4
```

Use:

```text
-
```

for an empty target group.

If the action has no target choices, enter:

```text
ok
```

to continue.

---

## Costs and mana payment

When a spell or ability has costs, the console may print:

```text
Cost: ...
Mana required: ...
Mana pool: ...
```

Relevant non-mana cost targets are selected through the command builder.

Mana source activation is currently handled automatically by the mana solver.

Before confirmation, the console can print:

```text
Auto-tap: <source IDs>
Pay mana: <mana payment>
```

The player therefore normally chooses the action and its targets/cost targets,
while the engine determines a valid mana-source activation plan automatically.

---

# 4. Combat controls

Combat declarations are entered as whole declarations and validated before they
mutate the game state.

## Declaring attackers

Basic syntax:

```text
attack <card>...
```

Example:

```text
attack white-2 white-5
```

If there is exactly one opposing player, that player is used as the default
defender.

### Shared defender

A defender can be supplied as the final argument:

```text
attack <card>... <defender>
```

Example:

```text
attack white-2 white-5 opponent
```

### Defender per attacker

Each attacker can instead specify its own defender:

```text
attack <card>:<defender>...
```

Example:

```text
attack white-2:opponent white-5:opponent
```

This is relevant for game states where multiple defending players or
planeswalkers may exist.

To declare no attackers:

```text
pass
```

A creature cannot be declared as an attacker more than once.

---

## Declaring blockers

Syntax:

```text
block <blocker>:<attacker>...
```

Example:

```text
block white-4:red-2 white-6:red-5
```

To declare no blockers:

```text
pass
```

A blocker cannot be declared as blocking multiple attackers in one declaration.

The complete declaration is validated before being accepted.

---

# 5. Player references

Players can be referenced by name or by these special references:

```text
self
me
opponent
```

`self` and `me` refer to the current player.

`opponent` works when exactly one other player matches.

---

# 6. Card references

Cards have stable global command IDs.

These IDs survive zone and controller changes and are therefore the preferred
way to reference cards in console commands.

A card can also be referenced by name when that name is unambiguous in the
current command scope.

If multiple cards share the same name, the console reports the ambiguity and
requires an explicit card ID.

Typical error:

```text
Ambiguous card '...'; use a card ID: ...
```

---

# 7. Console errors

Invalid input normally does not terminate the game.

Instead, the console reports the problem and allows the player to correct the
current command.

Possible errors include:

```text
Unknown or unavailable ability
Unknown mode
Unknown card
Ambiguous card
Specify a defender for each attacker
A creature cannot be declared twice
A creature cannot block multiple attackers
This mode has no legal targets or payable cost
```

During command building, invalid input keeps the player in the current stage.

---

# 8. Seeds

If no seed is specified, a random seed is generated.

A fixed seed can be supplied to reproduce a match:

```powershell
python -B -m game.bin.match --seed 123
```

Tournament rounds derive match seeds from the tournament base seed.

Both starting positions for one tournament round use the same match seed.

---

# 9. Logs

## Single match

For a detailed single-match log:

```powershell
python -B -m game.bin.match `
    --output runs/example.jsonl
```

The match runner can produce:

```text
example.jsonl
example.txt
```

The JSONL file is the structured source log.

The TXT file is a human-readable rendering.

## Tournament

Tournament output is configured using:

```text
--output
```

Individual match logs can be enabled using:

```text
--log-matches
```

Without `--log-matches`, the tournament can still produce aggregate summary
statistics without creating a detailed log for every game.

---

# 10. Common problems

## `PermissionError: 'runs'`

For `match.py`, `--output` expects a file path.

Correct:

```text
--output runs/match.jsonl
```

Incorrect:

```text
--output runs
```

## Output file already exists

Match logs are opened in exclusive-create mode and are not silently overwritten.

Choose a different filename or remove the old log first.
