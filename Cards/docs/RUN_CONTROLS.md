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
