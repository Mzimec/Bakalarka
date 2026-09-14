# Project Roadmap

## 1. Better Logging and Profiling

Improve experiment observability without producing excessive per-match output.

Planned improvements:

- clearer tournament summaries,
- per-agent decision timing,
- average / maximum decision time,
- match runtime statistics,
- optional detailed match logs,
- better error and termination diagnostics.

The goal is to make agent benchmarks easier to compare and profile.

---

## 2. Reduce Equivalent Action Generation

The current engine may generate strategically equivalent actions that differ only
in interchangeable game objects.

Examples:

- tapping `Island 1` instead of `Island 2`,
- selecting one of several identical creatures,
- casting one of two identical copies of the same card from hand,
- equivalent mana-payment combinations.

These actions increase the branching factor without adding meaningful strategic
choices.

Future action generation should identify interchangeable objects and collapse
such choices into equivalence classes where rules allow it.

This should reduce:

- candidate count,
- AI decision time,
- search complexity,
- LLM context size.

The engine must still preserve separate choices when the objects differ through
relevant state such as counters, attachments, damage, timestamps or other
characteristics.

---

## 3. Expand Card and Deck Coverage

Extend the current starter-deck card database with more strategically interesting
decks.

The next decks should emphasize interactions that are currently less important
in the existing environment, especially:

- instants,
- counterspells,
- removal,
- combat tricks,
- reactive abilities,
- control strategies,
- cards where passing priority is strategically useful.

This should create situations where the best action is sometimes to wait rather
than immediately use available mana.

Adding these decks will also naturally require implementation and testing of
additional Magic mechanics.

---

## 4. Improve Reactive Decision Making

Current agents are most comfortable in proactive board-development games.

A richer card pool should be accompanied by better handling of decisions such as:

- whether to pass while holding an instant,
- whether to respond to a spell,
- whether to save removal for a stronger threat,
- when to counter a spell,
- when to act during combat,
- when spending mana now is worse than preserving interaction.

These choices are especially important for evaluating control-oriented decks.

---

## 5. More Intelligent Agents

Continue developing stronger decision models on top of the same rules engine.

Possible directions include:

- improved heuristic evaluation,
- stronger modular policies,
- better LLM prompting and structured decisions,
- learned state-value functions,
- Monte Carlo Tree Search,
- hybrid search + heuristic/value models.

The shared rules engine and action-generation pipeline should remain independent
of the chosen decision model.

---

## 6. Smarter Search and Candidate Selection

Alongside removing equivalent actions, improve how agents explore the remaining
decision space.

Potential directions:

- better candidate pruning,
- heuristic action ordering,
- target-selection heuristics,
- variable search limits based on game complexity,
- exposing only strategically distinct mana plans,
- stronger combat search.

The aim is to spend computation on meaningful choices rather than on exhaustive
enumeration of near-identical actions.

