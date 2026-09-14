# Agents

## 1. Purpose

This document describes the decision-making components used by the MtG engine.

The engine distinguishes between two broad categories:

1. **Full agents** – controllers that decide what a player does during the game.
2. **Partial agents / decision modules** – components that solve one constrained
   subproblem for another controller, such as mana payment.

This distinction is important because not every component that makes a choice is
responsible for playing the whole game.

At the current stage, the main decision-making components are:

```text
Full agents
├── SimpleAgent
└── LLM-based agent

Partial decision modules
└── Mana solver
```

The rules engine remains authoritative.

Agents choose among actions that the engine exposes or validates; they do not
replace the rules implementation.

---

# 2. Agent boundary

An agent is responsible for player decisions.

The engine remains responsible for:

- legality,
- timing,
- target validation,
- cost validation,
- stack behavior,
- combat rules,
- continuous effects,
- replacement effects,
- state-based actions,
- trigger handling.

Conceptually:

```text
Game state
    ↓
legal decisions / candidate actions
    ↓
Agent
    ↓
chosen action
    ↓
ActionProcessor
    ↓
rules engine
```

An agent should therefore answer:

```text
"What legal action should this player choose?"
```

---

# 3. Full agents

A full agent controls a player throughout a match.

It may be asked to make decisions such as:

- whether to play a land,
- which spell to cast,
- which ability to activate,
- which targets to choose,
- whether to pass priority,
- which creatures to attack with,
- which creatures to block with,
- mulligan decisions,
- replacement-effect choices,
- ordering choices exposed by the rules engine.

A full agent may internally delegate smaller decisions to specialized modules.

For example:

```text
SimpleAgent
    ↓
chooses spell
    ↓
ManaSolver
    ↓
chooses how the cost can be paid
```

The mana solver is therefore not a second player-level agent.

---

# 4. SimpleAgent

`SimpleAgent` is the deterministic / heuristic baseline controller.

Its role is to provide:

- a non-human opponent,
- a reproducible baseline,
- a controller that does not require an external language model,
- a reference opponent for AI experiments,
- a fallback controller for automated tests and simulations.

The Simple agent operates directly on engine-level actions and game state.

It does not need to understand Oracle text. It works with the structured
representations already created by the engine.

---

## 5. SimpleAgent decision model

At a high level, the Simple agent follows a pipeline similar to:

```text
current State
    ↓
generate legal candidates
    ↓
assign heuristic value
    ↓
choose candidate
    ↓
execute through the normal engine
```

The important property is that the Simple agent evaluates **engine actions**,
not arbitrary text commands.

This makes it suitable as a baseline because its decisions can be inspected and
reproduced without a natural-language layer.

---

## 6. Candidate generation

Candidate generation is separated from final decision policy.

A candidate represents one possible engine action or declaration together with
information useful for selection.

Conceptually:

```text
Candidate
├── action
├── score / prior
├── description
└── optional key
```

Generation is bounded so that complex target combinations do not cause an
uncontrolled combinatorial explosion.

The current AI infrastructure can limit areas such as:

- number of target combinations,
- number of cost combinations,
- number of retained candidates per ability,
- number of combat assignments considered.

These limits are search controls, not game rules.

A legal action excluded by a search bound may still be legal according to the
engine.

---

## 7. SimpleAgent strengths and limitations

### Strengths

The Simple agent is useful because it is:

- fast compared with an external LLM,
- deterministic when given deterministic inputs,
- easy to run for many games,
- independent of prompt wording,
- suitable as a benchmark baseline,
- tightly integrated with engine-level legality.

### Limitations

Its quality is limited by its handcrafted evaluation policy.

It does not perform unrestricted strategic reasoning and does not understand a
card merely from natural-language rules text.

It can only reason about information represented by the engine and by its
implemented heuristics.

For this project, that is intentional: `SimpleAgent` acts primarily as a stable
baseline rather than as an attempt at optimal Magic play.

---

# 8. LLM-based agent

The LLM-based agent uses a language model as the high-level decision maker.

In the current project this is intended for local-model experimentation, such as
an Ollama-backed model.

The LLM still plays **inside the same rules engine**.

It does not get permission to mutate the game state directly.

Conceptually:

```text
State
    ↓
engine / adapter builds decision context
    ↓
LLM
    ↓
selected decision
    ↓
adapter resolves the choice to an engine action
    ↓
ActionProcessor
```

The rules engine therefore remains the source of truth even when the decision
comes from an LLM.

---

## 9. What the LLM decides

The LLM agent is intended to make strategic choices closer to player-level.

Depending on the current adapter, these can include decisions such as:

- which available action to take,
- whether to pass,
- which spell or ability to use,
- target selection,
- combat choices,
- other controller hooks exposed by the engine.

The exact choice space should be supplied by the engine or adapter rather than
invented by the model.

This prevents a language model from creating nonexistent cards, illegal targets,
or unsupported actions and treating them as valid engine actions.

---

## 10. What the LLM does not decide

The language model is not the rules engine.

It does not determine:

- whether a spell legally resolves,
- whether a target remains legal,
- how state-based actions work,
- whether a creature dies,
- how layers combine,
- whether a replacement effect applies,
- how the stack resolves.

Those decisions remain deterministic engine behavior.

The preferred architecture is:

```text
LLM proposes / selects
Engine validates and executes
```

rather than:

```text
LLM simulates the rules itself
```

---

## 11. Structured LLM decisions

For reliable integration, the LLM should operate over a constrained decision
space.

A useful decision context contains structured information such as:

```text
current player
phase / step
life totals
hand
battlefield
graveyards
stack
available legal actions
targets
combat state
```

The output should identify one of the offered choices or provide data that can
be resolved to one.

Free-form prose may be useful for reasoning or diagnostics, but the final action
needs to map back to a concrete engine object.

---

## 12. LLM failure handling

An LLM can produce:

- malformed output,
- an unknown choice,
- an illegal selection,
- a response that cannot be parsed,
- no usable answer.

The engine should not trust such output blindly.

The adapter should either:

- reject the response,
- retry according to its policy,
- fall back to a deterministic choice,
- pass priority,
- terminate through the configured decision/error limit.


---

# 13. Comparing Simple and LLM agents

The two agents differ primarily in their **decision policy**, not in the game
they are allowed to play.

Both ultimately operate through the same engine.

```text
                 ┌──────────────┐
State ──────────►│ legal choices │
                 └──────┬───────┘
                        │
            ┌───────────┴───────────┐
            │                       │
            ▼                       ▼
      SimpleAgent              LLM Agent
      heuristics               language model
            │                       │
            └───────────┬───────────┘
                        ▼
                  chosen action
                        │
                        ▼
                   rules engine
```

This is important for benchmarking.

When two agents are compared, differences in win rate should primarily come from
their decision policies rather than separate rule implementations.

---

# 14. Partial agents and specialized solvers

Not every decision-making component needs to control an entire player.

Some problems are better represented as specialized solvers.

Examples include:

- mana payment,
- target enumeration,
- combat assignment search,
- candidate pruning.

These components may be used by multiple full agents.

This keeps generic rule/search logic out of high-level strategic controllers. And reduce the game decision space.

---

# 15. Mana solver

The mana solver is the main current example of a partial decision module.

Its task is narrower than that of a full agent:

Given a mana requirement and the current available resources,
find a legal way to pay the cost.

It does not decide whether casting the spell is strategically good.

That decision belongs to a full agent.

---

## 16. SourceActivatingManaSolver

The current command/action infrastructure uses
`SourceActivatingManaSolver`.

Unlike a solver that considers only mana already present in the pool, this solver
can also plan activation of supported mana sources.

Conceptually it solves:

```text
current mana pool
+ available mana-producing sources
+ mana requirement
        ↓
payment plan
```

---

## 17. Mana-solving pipeline

A typical payment flow is:

```text
Ability / spell
    ↓
mana requirement
    ↓
SourceActivatingManaSolver
    ↓
find legal source activations
    ↓
produce mana
    ↓
construct payment
    ↓
cost execution
```

---

## 18. Supported mana solving

The current mana infrastructure supports important cost forms used by the
project, including:

- colored mana,
- generic mana,
- explicit colorless mana,
- hybrid mana,
- two-or-colored hybrid mana,
- Phyrexian mana,
- X costs.

The source-activating solver can also use supported mana-producing permanents
instead of requiring all mana to already be in the pool.

The search has been designed to avoid unnecessary enumeration of equivalent
payment combinations where possible.

---

## 19. Determinism

Mana payment is normally chosen deterministically from the legal plans found by
the solver.

This matters for:

- reproducible matches,
- benchmark stability,
- avoiding meaningless branching over strategically equivalent payments.

A deterministic mana payment policy also allows high-level agents to focus on
strategic decisions rather than repeatedly choosing between equivalent basic
land taps.

---

## 20. Why mana solving is separate

Keeping mana solving separate from full agents has several advantages.

### Reuse

The same payment logic can be used by:

- SimpleAgent,
- LLM agent,
- console player,
- tests,
- future search agents.

### Correctness

Mana payment remains based on one shared rules representation.

### Reduced action space

A high-level agent can choose to cast a card without separately treating every equivalent combination of tapped lands as a
different strategic action.

### Benchmark fairness

Two agents can use the same low-level payment solver, so a benchmark compares
their high-level decision quality rather than accidental differences in basic
mana payment enumeration.

---

## 21. Limits of the mana solver

The mana solver is deliberately narrower than the complete Magic mana rules.

The current ruleset does not claim general handling of:

- arbitrary mana abilities with complex costs,
- every conditional mana restriction,
- snow mana,
- all dependent mana production,
- every triggered mana interaction,
- every strategically meaningful choice between otherwise payable sources.

If a future experiment needs mana-source choice itself to be strategically
important, the solver interface may need to expose multiple payment plans rather
than automatically selecting one.

---

# 22. Agent composition

The intended architecture allows full agents to be composed from reusable
decision modules.

Conceptually:

```text
Full Agent
├── candidate generation
├── strategic policy
├── target selection
├── combat policy
└── mana solver
```

A different full agent can reuse most of these components while replacing only
the strategic policy.

For example:

```text
SimpleAgent
├── shared candidate generation
├── heuristic selection
└── shared mana solver

LLM Agent
├── shared candidate representation
├── LLM selection
└── shared mana solver
```

This is preferable to implementing each agent as a separate version of the game
rules.

---

# 23. Summary

The current agent architecture can be summarized as:

```text
                    MtG rules engine
                          │
                 legal decision space
                          │
           ┌──────────────┴──────────────┐
           │                             │
     SimpleAgent                    LLM Agent
     heuristics                     LLM policy
           │                             │
           └──────────────┬──────────────┘
                          │
                 shared sub-solvers
                          │
              SourceActivatingManaSolver
                          │
                          ▼
                     chosen action
                          │
                          ▼
                    rules execution
```

The central design principle is:

> Full agents decide strategy; shared specialized solvers handle constrained
> subproblems; the engine remains responsible for the rules.
