# Architecture

## 1. Purpose

This document describes architecture of this project. It tries to describe its components. This is not documentation for the code.

The central architectural idea is:

The game state is represented explicitly, legal actions are generated from that
state, actions are resolved through one shared pipeline, and all secondary
systems such as triggers, continuous effects, replacement effects and
state-based actions plug into that same flow.

---

# 2. High-level view

The engine can be viewed as several cooperating layers:

```text
                    ┌─────────────────────┐
                    │ Controllers / Agents│
                    │ Human, Simple, LLM  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Action Generation   │
                    │ legal choices/plans │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Action Processing   │
                    │ validation + costs  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Resolution Engine   │
                    │ effects/operations  │
                    └──────────┬──────────┘
                               │
               ┌───────────────┼────────────────┐
               │               │                │
               ▼               ▼                ▼
        ┌────────────┐   ┌─────────────┐  ┌─────────────┐
        │ Event Bus  │   │ Replacement │  │ Continuous  │
        │ + Triggers │   │ Effects     │  │ Effects     │
        └─────┬──────┘   └─────────────┘  └─────────────┘
              │
              ▼
        ┌────────────┐
        │ SBA Resolver│
        └─────┬──────┘
              │
              ▼
        ┌────────────┐
        │ Game State │
        └────────────┘
```

The game state is not owned by any one rule subsystem.

Instead, the state is the shared runtime model on which the remaining systems
operate.

---

# 3. Core runtime data model

The most important runtime classes are:


- State
- Player
- Card


Together they represent the actual game position.

---

## 3.1. State

`State` is the central runtime container for one game.

It holds or coordinates information such as:

- players,
- cards and permanents,
- zones,
- the current turn and phase,
- the stack,
- combat state,
- active continuous effects,
- replacement rules,
- runtime indexes and registers,
- other rule-specific state.

Conceptually:

```text
State
├── players
├── card storage / registers
├── stack
├── turn / phase information
├── combat
├── continuous effects
├── replacement effects
└── indexes used by queries
```

`State` is intentionally a data model rather than the component responsible for
making strategic choices.

Rules systems inspect and mutate the state through explicit operations and
resolution logic.

---

## 3.2. Player

`Player` represents one participant in the game.

A player contains game data such as:

- life total,
- library,
- hand,
- graveyard,
- battlefield ownership relationships,
- mana pool,
- controller,
- poison counters and other player-level state.

The `controller` is the decision-making component associated with that player.

This controller may be:

- a human console controller,
- `SimpleAgent`,
- an LLM-based agent,
- another future AI controller.

The player object itself does not contain strategic AI logic.

---

## 3.3. Card

The engine separates immutable card definition data from the runtime card object.

### CardDefinition

`CardDefinition` describes what a card fundamentally is.

Typical definition-level information includes:

- name,
- mana cost,
- card types,
- subtypes,
- printed power and toughness,
- keywords,
- abilities,
- static continuous rules,
- replacement effects,
- loyalty,
- legendary status.

A `CardDefinition` is shared data.

It should not contain mutable per-game state.

### Card

`Card` is the runtime instance of a definition.

Conceptually:

```text
Card
├── definition
├── owner
├── current controller
├── current zone
├── counters
├── attachment state
├── runtime identity
└── other mutable state
```

Several runtime cards may use the same `CardDefinition`.

This distinction allows deck definitions to remain immutable while each game
instance evolves independently.

---

## 3.4. Object identity and zone changes

Runtime identity matters heavily in Magic.

A card that leaves one zone and later enters another is not always considered
the same game object for rules purposes.

The engine therefore distinguishes between:

- persistent card identity,
- the current runtime incarnation of that object.

This is important for systems such as:

- targeting,
- delayed effects,
- replacement effects,
- loyalty activation state,
- last known information,
- attachments.

A target chosen before a zone change must not silently begin referring to a new
incarnation of the same physical card.

---

## 3.5. Zones

Zones are represented explicitly.

Important zones include:

```text
LIBRARY
HAND
BATTLEFIELD
GRAVEYARD
EXILE
```

Zone changes are performed through operations rather than by arbitrary movement
of cards between Python containers.

This allows the engine to consistently handle:

- enter-the-battlefield triggers,
- leave-the-battlefield triggers,
- dies triggers,
- replacement effects,
- attachment cleanup,
- runtime incarnation changes,
- register/index updates.

---

## 3.6. Registers and object storage

The engine maintains dedicated runtime storage and indexes instead of repeatedly
scanning every object in the game.

At the lowest level, object storage maps internal IDs to runtime objects.

Conceptually:

```text
ObjectStorage

id ─────► runtime object
```

Registers build searchable views over those stored objects.

Examples include:

```text
CardRegister
PlayerRegister
```

Registers are responsible for keeping indexed information synchronized with the
current state of runtime objects.

---

# 4. Query engine

The query engine provides a generic way to select runtime objects.

Instead of implementing every search as:

```text
loop over every card
    if condition A
    and condition B
    and condition C
```

the engine uses indexed queries.

The important concepts are:


- IndexKey
- IndexProvider
- BitSet
- Query

---

## 4.1. Indexes and BitSets

For an indexed property, the engine keeps groups of object IDs.

For example, conceptually:

```text
ZONE
├── HAND        -> BitSet(...)
├── BATTLEFIELD -> BitSet(...)
└── GRAVEYARD   -> BitSet(...)

CONTROLLER
├── Player A -> BitSet(...)
└── Player B -> BitSet(...)
```

A `BitSet` is an integer-backed set representation.

This makes common set operations efficient:

- intersection
- union
- difference

For example:

```text
creatures
AND
controlled by Player A
AND
on battlefield
```

can be evaluated largely as intersections of bitsets rather than repeated object
iteration.

---

## 4.2. Query objects

Queries express what objects are required.

Important query forms include concepts such as:

- EqQuery
- InQuery
- AndQuery
- OrQuery

The same query system is useful across many parts of the engine:

- card lookup,
- target generation,
- continuous effects,
- controller restrictions,
- combat-related selection,
- rules checks.

This prevents every subsystem from creating its own ad-hoc object-search logic.

---

## 4.3. Why the query system matters

The query system serves two purposes.

### Performance

Indexed bitsets avoid unnecessary full scans of all runtime objects.

### Consistency

More importantly, systems share the same meaning for concepts such as:

```text
cards on battlefield
cards controlled by player
cards with a specific property
```

That makes the query engine part of the architecture rather than merely a
performance optimization.

---

# 5. Abilities

Abilities are described by immutable definitions and converted into runtime
abilities when needed.

Important concepts include:

- AbilityDefinition
- SubAbilityDefinition
- SubAbilityComposer

An ability definition describes things such as:

- mana cost,
- additional costs,
- effects,
- whether it uses the stack,
- allowed zones,
- modes,
- targets,
- variable values such as X.

The runtime form binds that definition to:

- a source,
- a controller,
- the current game state.

---

## 5.1. SubAbilities

Complex abilities are composed from smaller pieces.

A `SubAbilityDefinition` groups concepts such as:

- action node
- target slots
- effects
- resolution behavior

This lets an ability be built from reusable rule components instead of requiring
a custom executor for every card.

Conceptually:

```text
AbilityDefinition
    │
    ├── cost subabilities
    │
    └── action subabilities
```

The same composition system is used by both human command construction and AI
action generation.

---

## 5.2. Action nodes

Action nodes represent logical combinations of choices.

They make a decision tree of ability for its generation.

For example ability:

```text
Choose one:
    pay X mana
    sacrifice a creature
```

can be represented without hardcoding that choice into the outer game loop.

The generated action-node option becomes one part of the full execution plan.

---

# 5.3. Target slots

Targets are represented as named slots.

A target slot describes:

- what kind of object can be selected,
- how many objects may be selected,
- whether targets must be distinct,
- relationships to other slots.

Conceptually:

```text
TargetSlot
├── TargetSpec
├── TargetSelector
└── constraints
```

A target slot is a description of a requirement.

The actual chosen objects are stored separately in a target binding.

---

# 5.4. Target specifications

A target specification determines which runtime objects are candidates.

It may use:

- the query engine,
- controller relationships,
- zone restrictions,
- type restrictions,
- custom rule conditions.

In the engine is mainly used QuerySpec, others are relicts from before a query system came to exist.

---

## 5.5. Target bindings

A target binding records the concrete targets selected for a generated action.

Conceptually:

```text
TargetBinding
    slot A -> object(s)
    slot B -> object(s)
```

The binding can later be frozen into an immutable representation for execution.

---

# 6. Action generation pipeline

The action-generation subsystem converts an ability into executable legal plans.

At a high level:

```text
Ability
    ↓
compose subability
    ↓
generate action-node options
    ↓
generate target bindings
    ↓
solve costs / mana
    ↓
ExecutionPlan
```

Important classes include:

- ActionGenerationContext
- ExecutionPlanPipeline
- ExecutionPlanStrategy
- ActionNodeOptionGenerator
- TargetBindingGenerator

The key architectural idea is that generation strategy is configurable.

The same underlying ability can therefore be explored differently by:

- console commands,
- SimpleAgent,
- Modular/LLM decision code,
- tests,
- future search algorithms.

---

## 6.1. ExecutionPlanStrategy

`ExecutionPlanStrategy` tells the action-generation pipeline how choices should be
produced.

Conceptually it combines:

```text
how to choose modes
+
how to choose targets
+
how to solve mana/costs
```

For example:

- SelectedActionNodeOptionGenerator
- FullTargetBindingGenerator
- SourceActivatingManaSolver

can together mean:

> Use this particular mode, enumerate legal targets, and find a valid mana
> payment including mana-source activation.

This is one of the main points where high-level decision policy is separated from
rules legality.

---

## 6.2. Execution plans

An execution plan is a concrete realization of an ability.

It contains enough information to execute one legal version of that action.

Conceptually:

```text
ExecutionPlan
├── selected mode
├── target binding
├── selected effects
├── cost information
└── mana solution
```

A single spell or activated ability may therefore generate many execution plans.

They differ because of choices such as:

- mode,
- targets,
- cost targets,
- X value,
- mana payment.

---

## 6.3. Mana solving inside action generation

Mana payment is integrated into action generation rather than being a completely
separate execution path.

The main current solver is SourceActivatingManaSolver.

It can combine:

- mana already in the pool,
- supported available mana sources,
- the ability's mana requirement.

Its job is not to decide whether an action is strategically good.

Its job is to determine whether and how the cost can legally be paid.

Detailed agent behavior is described in [AGENTS.md](AGENTS.md).

---

# 7. Game actions

Once all required choices are known, the engine represents the player's decision
as a game action.

Examples include:

- casting a spell,
- activating an ability,
- passing priority,
- playing a land,
- declaring attackers,
- declaring blockers,
- conceding.

The action is the boundary between decision making and rules execution.

Controllers choose actions.

The game engine executes them.

---

## 7.1. Action processing

The action-processing layer validates and commits a chosen action.

Conceptually:

```text
chosen action
    ↓
validate current legality
    ↓
validate/reserve costs
    ↓
pay costs
    ↓
place on stack or resolve immediately
    ↓
process resulting rules consequences
```

Important rule:

> Generation-time legality is not assumed to remain valid forever.

Before execution, the action is validated again against the current state.

This matters because the game state may have changed between generation and
commitment.

---

## 7.2. ResolutionContext

Operations need contextual information about why they are being executed.

`ResolutionContext` carries information such as:

- source,
- controller,
- ability,
- selected targets,
- whether the operation belongs to a cost.

This avoids requiring every effect and operation to recover that information
independently from global state.

---

## 7.3. Effects and operations

The architecture separates Effect from Operation.

An `Effect` describes what an ability wants to do.

An `Operation` is a concrete state-changing action that can be executed.

For example:

```text
Effect
    "deal 3 damage to target"

        ↓ generates

Operation
    DamageOperation(target, 3)
```

This separation is important because operations can then pass through shared
systems such as:

- replacement effects,
- event generation,
- simultaneous execution,
- rollback/checkpoint logic.

---

## 7.4. OperationExecutor

`OperationExecutor` is the central mechanism that performs concrete game
operations.

Conceptually:

```text
Operation
    ↓
replacement processing
    ↓
execute state change
    ↓
GameEvent(s)
```

Operations include things such as:

- moving a card,
- dealing damage,
- changing life,
- creating tokens,
- attaching permanents,
- modifying counters.

The engine prefers explicit operations over direct mutation because operations
create a consistent interception point for the rest of the rules system.

---

## 7.5. Resolution engine

The resolution engine coordinates effect and operation execution.

It is responsible for turning the already chosen action into actual game-state
changes.

At a high level:

```text
resolved action
    ↓
effect sequence
    ↓
operations
    ↓
replacement resolver
    ↓
operation executor
    ↓
events
    ↓
triggers / SBA
```

This shared path is used by spells, abilities and other supported rules actions.

---

## 7.6. Stack and priority

The stack is part of `State`, while priority behavior is handled by the priority
system/game loop.

Conceptually:

```text
player action
    ↓
uses stack?
 ┌───────┴───────┐
 yes             no
 │                │
 ▼                ▼
stack          immediate
 │             resolution
 ▼
players receive priority
 │
all pass
 ▼
top object resolves
```

Mana abilities and land plays are examples of actions that bypass the normal
stack path.

---

## 7.7. Event system

State changes produce `GameEvent` objects.

An event describes something that happened, for example:

- card moved
- damage dealt
- spell cast
- step began

The event system decouples performing the change from reacting to the change.
This is important for triggered abilities.

The operation that moves a creature to the graveyard does not need to know every
possible card in the game that may trigger from a creature dying.

It only emits the relevant event.

---

## 7.8. EventBus

The `EventBus` collects and distributes game events to the rules systems that
need them.

Events are also useful for:

- structured match logs,
- debugging,
- trigger collection,
- last-known-information snapshots.

The event bus is not responsible for strategic decisions.

It is part of the deterministic rules infrastructure.

---

## 7.9. Trigger system

Triggered abilities are derived from emitted game events.

Important concepts include:

- TriggeredAbilityDefinition
- TriggeredAbility
- TriggerResolver

The general flow is:

```text
GameEvent
    ↓
find matching trigger definitions
    ↓
capture trigger + relevant snapshot
    ↓
APNAP ordering / controller choices
    ↓
place triggered ability appropriately
```

Capturing the trigger at event time is important because the source may later
leave the battlefield or change characteristics.

---

## 7.10. Last known information

Some rules need information about an object as it existed immediately before a
change.

The event and trigger pipeline therefore preserves supported last-known data,
such as:

- previous controller,
- previous zone,
- previous types.

This information belongs to the rules event pipeline rather than to controller
AI.

---

## 7.11. State-based actions

After relevant game actions and resolutions, the engine checks state-based
actions.

Conceptually:

```text
operation(s)
    ↓
events
    ↓
state-based actions
    ↓
new events / changes?
    ↓
repeat until stable
```

This system handles rule consequences such as:

- lethal damage,
- zero toughness,
- zero loyalty,
- player loss,
- legend rule,
- invalid attachments,
- counter cancellation,
- token disappearance.

State-based actions are part of the shared resolution cycle rather than custom
cleanup code inside individual cards.

---

# 8. Combat subsystem

Combat has its own runtime state, but it still uses the common action and
resolution architecture.

The combat system tracks information such as:

- attacking creatures,
- defenders,
- blockers,
- blocked state,
- combat damage steps.

Attackers and blockers are declared as complete mappings and validated before
being applied.

This avoids partially mutating combat state while a declaration is still being
constructed.

Combat damage is then represented using the shared damage operation system.

---

# 9. Continuous effects

Continuous effects modify how game objects are seen by the rules engine.

Important concepts include:

- StaticContinuousRule
- ContinuousEffectDefinition
- ContinuousEffect
- Modifier
- LayeredModifier

A continuous effect combines:

- which objects are affected
- which properties are modified
- how long the effect exists

Continuous effects are not applied by permanently rewriting the underlying card
definition.

Instead, current characteristics are derived from base values plus active
modifiers.

---

## 9.1. Modifiable statistics

Properties that can be changed by continuous effects are represented as
statistics.

Examples include:

- POWER
- TOUGHNESS
- TYPES
- SUBTYPES
- ABILITIES
- KEYWORDS
- CONTROLLER
- COLORS
- MANA_COST

The exact set of statistics is defined centrally through the stat system.

This gives modifiers one shared interface instead of requiring separate custom
logic for every property.

---

## 9.2. Modifiers

Modifiers describe how a statistic changes.

Conceptually supported modifier forms include operations such as:

- set value
- add integer
- multiply value
- add to set
- remove from set
- clamp

A modifier is deliberately smaller than a complete continuous effect.

The continuous effect determines where and when,

while the modifier determines what transformation to apply.

---

# 9.3. Layers

Continuous effects are evaluated through the layer system.

Important layer categories include:

- COPY
- CONTROL
- TEXT
- TYPE
- COLOR
- ABILITY
- PT_CDA
- PT_SET
- PT_MODIFY
- PT_SWITCH
- RULES

The layer engine processes characteristics in order rather than repeatedly
recomputing the whole game until it reaches a fixed point.

Within a layer, ordering is determined using:

- dependencies where supported,
- timestamps,
- stable creation order.

Detailed rule behavior belongs in [ENGINE_ROADMAP.md](ENGINE_ROADMAP.md).

Architecturally, the important point is that all current characteristic queries
see the result of this shared layered evaluation.

---

# 9.4. Continuous-effect dependencies

Some effects in the same layer depend on the result of others.

Definitions can therefore describe dependencies.

The engine also derives supported dependency relationships for specific cases.

The dependency system is intentionally constrained.

It is not a generic analyzer of arbitrary Python code.

This keeps the layer evaluator predictable and testable.

---

# 9.5. Refreshing continuous effects

Continuous effects are refreshed against the actual runtime objects and
registers.

Earlier layers can affect which objects later layers see.

An effect that acts in multiple layers preserves the appropriate recipient set
according to the supported rules model.

The system synchronizes indexes with intermediate states rather than cloning the
whole `State` for every layer.

---

## 9.6. Replacement effects

Replacement effects intercept operations before the original operation is
executed.

Important classes include:

- ReplacementEffectDefinition
- ReplacementEffect
- ReplacementResolver

The pipeline is conceptually:

```text
original operation
    ↓
find applicable replacements
    ↓
affected player chooses if needed
    ↓
transform operation
    ↓
re-evaluate replacement applicability
    ↓
execute resulting operation(s)
```

Replacement effects therefore belong immediately before concrete operation
execution.

---

# 9.7. Replacement definitions and runtime instances

A replacement definition describes the rule.

A runtime `ReplacementEffect` additionally stores per-game state such as:

- source,
- source incarnation,
- remaining uses,
- prevention/shield capacity,
- duration.

This distinction mirrors the general pattern used throughout the engine:

```text
immutable definition
        ↓
runtime instance
```

---

## 9.8. Replacement chaining

After a replacement transforms an operation, the resolver evaluates the new
operation again.

For example:

```text
move to graveyard
    ↓
replace with exile
    ↓
new operation: move to exile
    ↓
check replacements again
```

The resolver tracks which effects have already affected an operation lineage so
that one replacement cannot incorrectly apply to itself forever.

---

## 9.10. Attachments, tokens and planeswalkers

These mechanics are not separate mini-engines.

They are integrated through the common systems.

### Tokens

Token creation uses operations and therefore participates in:

- events,
- ETB triggers,
- replacement effects,
- state-based actions.

### Attachments

Auras and Equipment use shared:

- card state,
- modifiers,
- targeting,
- operations,
- state-based-action validation.

### Planeswalkers

Planeswalkers reuse:

- counters,
- combat defenders,
- damage operations,
- state-based actions,
- activated abilities.

This reuse is intentional.

The architecture prefers extending existing concepts over creating one isolated
execution path per mechanic.

---

# 10. Console layer

The console is an adapter around the same underlying engine.

It does not implement separate game rules.

Important console components include:

- ConsoleDecisionMaker
- CommandSession
- command parser
- combat declaration parser
```

The console converts user input into the same game actions that AI controllers
use.

For example:

```text
user types command
    ↓
CommandSession constructs legal choices
    ↓
action-generation pipeline validates them
    ↓
GameAction
    ↓
normal action processor
```

This keeps human and AI games behaviorally aligned.

---

# 11. Controller layer

Controllers are intentionally outside the rules core.

A controller should pick in specific situation an action from pregenerated actions by the engine.

The rules engine decides whether the returned answer is legal and what happens
afterward.

Current controller-related documentation is in [AGENTS.md](AGENTS.md)

---

# 12. Game loop

The game loop coordinates the progression of the match.

It is responsible for high-level sequencing such as:

- setup,
- turns,
- steps and phases,
- priority windows,
- asking the current controller for decisions,
- advancing after passes,
- ending the game.

It does not itself implement every rule effect.

Instead, it delegates to specialized systems such as:


- ActionProcessor
- ResolutionEngine
- PrioritySystem
- CombatState
- SBAResolver
- TriggerResolver

This keeps turn progression separate from card-effect logic.

---

# 13. Match and simulation layer

The simulation layer sits above the game engine.

Its purpose is to run complete games for:

- testing,
- AI comparison,
- benchmark experiments,
- human play.

Important entry points include:


- run_match()
- run_tournament()

`run_match()` runs one game.

`run_tournament()` repeatedly invokes matches between two already configured
participants and aggregates results.

The simulation layer is deliberately separate from the rules core.

Changing benchmark logging should not require changing combat or spell
resolution.

---

# 14. Logging

Structured logging observes the normal resolution pipeline.

Logged wrappers can capture information such as:

- decisions,
- operations,
- events,
- result metadata,
- seeds,
- timings.

Logging is not part of game legality.

A game should behave identically whether persistent logging is enabled or
disabled.

---

# 15. Game Flows

Diagrams of engine pipelines.

---

## 15.1 Typical Spell Flow

A complete spell can be viewed as:

```text
Controller decides to cast
        ↓
AbilityDefinition
        ↓
SubAbility composition
        ↓
ExecutionPlanPipeline
        ↓
mode / targets / mana solution
        ↓
GameAction
        ↓
ActionProcessor
        ↓
pay costs
        ↓
put spell on stack
        ↓
priority passes
        ↓
ResolutionEngine
        ↓
Effect(s)
        ↓
Operation(s)
        ↓
ReplacementResolver
        ↓
OperationExecutor
        ↓
GameEvent(s)
        ↓
TriggerResolver
        ↓
SBAResolver
        ↓
updated State
```

This is the most useful mental model for understanding the engine.

---

## 15.2. Typical combat flow

Combat follows the same philosophy:

```text
GameLoop enters combat
        ↓
controller declares attackers
        ↓
CombatState validates whole declaration
        ↓
controller declares blockers
        ↓
CombatState validates whole declaration
        ↓
damage assignment
        ↓
damage operations
        ↓
ReplacementResolver
        ↓
OperationExecutor
        ↓
events / triggers / SBA
```

Combat is specialized in how declarations and assignment are generated, but not
in how damage or resulting rule consequences are resolved.

---

## 15.3. Typical continuous-effect query

When the engine needs a current characteristic such as power:

```text
Card base value
    ↓
active continuous rules
    ↓
target selection for current layer
    ↓
ordered modifiers
    ↓
layered evaluation
    ↓
current power
```

The base card object therefore does not need to be permanently rewritten every
time an aura, counter, keyword or static ability changes its characteristics.

---

# 15.4. Typical replacement flow

When an operation is about to happen:

```text
Operation
    ↓
ReplacementResolver
    ↓
applicable replacement definitions
    ↓
controller choice if required
    ↓
transform
    ↓
replacement search again
    ↓
final operation(s)
    ↓
OperationExecutor
```

This guarantees that replacement rules interact with all supported operations in
one consistent place.

---

# 16. Architectural boundaries

The project intentionally separates several concerns.

## 16.1 Data

- State
- Player
- Card
- definitions
- runtime objects

## 16.22 Search and selection

- queries
- target specs
- action generation
- mana solver

## 16.3. Execution

- GameAction
- ActionProcessor
- ResolutionEngine
- OperationExecutor

## 16.4. Rule reactions

- EventBus
- TriggerResolver
- SBAResolver
- ReplacementResolver
- continuous-effect evaluator

## 16.5 Decisions

- human controller
- SimpleAgent
- LLM agent

## 16.6. Experiment infrastructure

- run_match
- run_tournament
- logging
- summaries

Keeping these boundaries clear is one of the main design goals of the engine.

