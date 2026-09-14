# MtG Engine Ruleset

## 1. Scope

This document describes the ruleset currently implemented by the MtG engine.

The engine is designed for manually defined cards and mechanics built on the
existing runtime model:

```text
CardDefinition
    ↓
runtime Card instance
    ↓
Ability / Effect
    ↓
Operation
    ↓
Resolution engine
```

The implementation is intended to support deterministic two-player games,
starter-deck experiments, AI benchmarking and manually authored mechanics.

It is **not**:

- a complete implementation of Magic: The Gathering,
- an Oracle text interpreter,
- a full card database,
- a rules oracle for arbitrary real cards,
- a general parser for Comprehensive Rules text.

The main rules reference is the official Magic: The Gathering Comprehensive Rules,
especially the areas covering:

- mana and costs,
- permanents and card types,
- turn structure,
- casting and activating abilities,
- resolution,
- keyword abilities,
- state-based actions,
- continuous effects,
- replacement effects.

The engine intentionally implements only the subset needed by the supported
cards and experiments.

---

# 2. Core rules model

The rules engine uses a shared execution pipeline.

```text
controller chooses action
        ↓
ActionProcessor
        ↓
cost validation/payment
        ↓
stack or immediate resolution
        ↓
ResolutionEngine
        ↓
OperationExecutor
        ↓
GameEvent generation
        ↓
triggers
        ↓
state-based actions
```

Game rules are represented explicitly in code rather than inferred from card
text.

New mechanics are expected to use the same action, operation, event, stack and
state-based-action infrastructure instead of creating separate execution paths.

---

# 3. Turn structure

The engine supports the normal duel turn structure:

```text
Untap
Upkeep
Draw

Precombat Main

Beginning of Combat
Declare Attackers
Declare Blockers
Combat Damage
End of Combat

Postcombat Main

End Step
Cleanup
```

Implemented behavior includes:

- untap without priority,
- upkeep,
- draw step,
- both main phases,
- combat,
- end step,
- cleanup,
- the first player skipping the first draw step.

Mana pools are emptied at appropriate phase and step boundaries.

Cleanup includes:

- discarding down to maximum hand size,
- removing marked damage,
- expiration of temporary effects,
- exceptional priority and repeated cleanup when rules actions require it.

---

# 4. Priority and the stack

The engine implements a shared priority and stack model.

After a player takes an action, that player retains priority.

When all players pass in succession:

- if the stack is non-empty, the top object resolves;
- if the stack is empty, the game advances as appropriate.

After an object resolves, priority returns to the active player.

Mana abilities do not use the stack.

Land plays are special actions and do not use the stack.

Triggered mana abilities resolve immediately without using the stack.

---

# 5. Lands

Lands are played as special actions.

A land play requires:

- the card to be in the player's hand,
- the player to have priority,
- a main phase,
- an empty stack,
- the player's land-play limit not to have been exhausted.

The land-play count resets with the turn as required by the supported ruleset.

Basic land handling includes:

- Plains,
- Island,
- Swamp,
- Mountain,
- Forest,
- Wastes.

Basic land subtypes grant their intrinsic mana abilities.

Wastes produces explicitly colorless mana and is not represented using a
fabricated basic land subtype.

---

# 6. Mana

The engine has a single shared mana representation used by:

- card costs,
- action generation,
- mana pools,
- mana payment,
- AI planning,
- console action construction.

Supported mana-cost forms include:

- colored mana,
- generic mana,
- explicit colorless mana,
- hybrid mana,
- two-or-colored hybrid mana,
- Phyrexian mana,
- X costs.

The mana solver can:

- pay from the existing mana pool,
- activate ordinary supported mana sources,
- select deterministic payment plans,
- handle supported indivisible multi-mana producers.

The solver is intentionally not a complete implementation of all possible mana
ability interactions.

Currently outside the general model are cases such as:

- arbitrary mana abilities with complex additional costs,
- unrestricted dependent mana output,
- snow mana,
- restricted mana,
- all possible mana-trigger interactions during payment.

---

# 7. Costs

Costs are paid before effects.

Before an action is committed, the engine validates the complete stored action
again.

The cost system tracks resources such as:

- mana,
- life,
- tapped objects,
- sacrificed or otherwise consumed objects,
- loyalty.

Cost payment uses a transactional checkpoint for supported runtime state.

If a later supported cost fails, earlier supported payments can be rolled back.

Custom costs are expected to expose a pure reservation/preflight mechanism rather
than mutate arbitrary state during validation.

The transaction system is not intended as a universal rollback mechanism for
arbitrary Python callbacks or external state.

---

# 8. Casting and activating abilities

Spells and activated abilities are represented through `AbilityDefinition` and
the shared action-generation pipeline.

The system supports:

- timing validation,
- costs,
- modes,
- targets,
- generated execution plans,
- stack resolution,
- abilities that resolve immediately where appropriate.

Loyalty abilities are represented through the same ability system.

---

# 9. Targets

Targeting supports:

- legal target generation,
- target validation during action creation,
- target validation again during resolution,
- shroud,
- hexproof,
- object incarnation changes after zone changes.

If some targets become illegal, the effect continues using the remaining legal
targets where supported.

If all targets of a spell or ability become illegal, that object does not resolve
its effects.

Objects that leave and re-enter a zone are treated as new incarnations.

---

# 10. Triggers

The engine supports triggered abilities for events including:

- entering the battlefield,
- leaving the battlefield,
- dying,
- damage,
- casting,
- beginnings of steps and phases.

Triggered abilities are captured at the time their triggering event occurs.

The engine preserves relevant last-known information so that a trigger is not
lost merely because the source later:

- leaves the battlefield,
- changes controller,
- changes characteristics.

Supported trigger behavior includes:

- APNAP ordering,
- controller choices,
- intervening-if conditions,
- delayed triggers,
- reflexive triggers,
- state triggers,
- triggered mana abilities.

Simultaneous zone changes share before/after snapshots, allowing effects such as
a dying watcher to observe other objects dying simultaneously.

---

# 11. Last known information

The runtime records information needed for supported last-known-information
rules, including:

- previous controller,
- previous zone,
- previous card types.

This is used when a trigger or rule needs information about an object that no
longer exists in the same runtime state.

The implementation is not yet a general historical-object query system.

---

# 12. Combat

Combat uses complete declarations rather than incremental mutation while a player
types choices.

## Attackers

Attacker validation includes:

- controller,
- battlefield zone,
- tapped state,
- summoning sickness,
- attack restrictions,
- valid defending object.

A creature may attack:

- the defending player,
- a supported opposing planeswalker.

If the defending planeswalker disappears before combat damage, the attacker
remains an attacking creature but does not redirect damage to that player's
controller.

## Blockers

Blocker declarations are validated as a group.

Supported restrictions include:

- flying/reach,
- menace,
- defender-related attack restrictions,
- explicit attack/block restrictions.

If a blocker disappears after blocking, the attacker remains blocked.

---

# 13. Combat damage

Combat damage is assigned and dealt simultaneously within a combat-damage step.

Supported mechanics include:

- first strike,
- double strike,
- trample,
- deathtouch,
- lifelink,
- infect,
- wither.

The engine does not use the obsolete blocker damage-order rule.

Damage allocation can be delegated to controller logic where a choice exists.

---

# 14. Damage

Damage is represented through shared damage operations.

Damage may affect:

- players,
- creatures,
- planeswalkers.

Supported damage-related characteristics include:

- combat damage flag,
- lifelink snapshot,
- deathtouch snapshot,
- infect snapshot.

Damage replacement preserves these characteristics when creating replacement
damage operations.

`unpreventable=True` prevents prevention effects from preventing that damage,
but does not disable unrelated replacement effects such as damage multiplication.

---

# 15. Keywords

Currently supported keywords and related combat rules include:

- haste,
- vigilance,
- defender,
- flying,
- reach,
- menace,
- first strike,
- double strike,
- trample,
- deathtouch,
- lifelink,
- indestructible,
- infect,
- wither.

Keywords are normalized strings, for example:

```text
"double strike"
```

Keyword state is live and can be modified by continuous effects.

---

# 16. State-based actions

The engine evaluates state-based actions through the shared rules pipeline.

Supported state-based actions include:

- creature toughness of zero or less,
- lethal damage,
- lethal deathtouch damage,
- indestructible interaction,
- player losing at zero or less life,
- player losing after attempting to draw from an empty library,
- player losing with ten or more poison counters,
- planeswalker with zero loyalty,
- legend rule,
- illegal attachments,
- cancellation of equal +1/+1 and -1/-1 counters,
- token disappearance after leaving the battlefield.

Replacement effects may replace supported state-based-action operations.

A pathological replacement interaction that prevents the game from reaching a
stable state terminates with an error rather than looping forever.

---

# 17. Counters

Supported counters include:

- +1/+1 counters,
- -1/-1 counters,
- loyalty counters,
- poison counters.

+1/+1 and -1/-1 counters modify power and toughness through the modifier system.

Equal numbers of +1/+1 and -1/-1 counters cancel as a state-based action.

Loyalty is handled independently from P/T counters.

---

# 18. Tokens

Tokens use the same permanent and event infrastructure as ordinary cards.

Supported token behavior includes:

- creating multiple tokens simultaneously,
- ETB triggers,
- leave and dies triggers,
- summoning sickness,
- combat,
- attachments,
- counters,
- continuous effects.

`CreateTokenOperation` exposes the created token group through `.created`.

After a token leaves the battlefield, it cannot continue moving normally through
zones and disappears during subsequent state-based-action processing.

General token-copy rules are not yet implemented.

---

# 19. Attachments

The engine supports a shared attachment model for:

- Auras,
- Equipment.

## Auras

Aura spells target an object while being cast.

Auras can also enter attached without targeting through supported explicit
operations.

Non-targeted attachment ignores shroud and hexproof, but still checks the aura's
enchant restriction.

The default supported aura model is creature attachment.

Custom `enchant(state, aura, host)` logic can define a persistent legality
restriction.

## Equipment

Equipment can use an equip ability with its own mana cost.

Equip:

- uses the stack,
- follows sorcery timing,
- moves the Equipment between legal hosts.

A change of controller of the equipped creature does not by itself detach the
Equipment.

Attachments are detached appropriately during zone changes.

Illegal attachments are handled by state-based actions.

---

# 20. Planeswalkers

Supported planeswalker behavior includes:

- initial loyalty,
- loyalty counters,
- damage reducing loyalty,
- state-based removal at zero loyalty,
- planeswalkers as attack defenders,
- creatures blocking attackers attacking a planeswalker.

If a planeswalker is also a creature, shared permanent damage logic applies and
the planeswalker also loses loyalty from damage.

Trample damage does not automatically carry through a planeswalker to its
controller.

---

# 21. Loyalty abilities

`AbilityDefinition(loyalty_cost=...)` supports:

- positive loyalty costs,
- negative loyalty costs,
- zero loyalty costs.

Loyalty abilities:

- use sorcery timing,
- may be activated only once per permanent per turn,
- pay loyalty before resolution,
- reset the per-turn activation state after an appropriate zone change.

The limit belongs to the permanent, not individually to each loyalty ability.

---

# 22. Infect and wither

## Infect

Damage from a source with infect:

- gives poison counters to players,
- gives -1/-1 counters to creatures.

Ten poison counters cause a player to lose.

Lifelink still applies to infect damage.

## Wither

Damage from a source with wither:

- gives -1/-1 counters to creatures,
- deals normal life-loss damage to players.

---

# 23. Continuous effects and layers

The continuous-effect system follows the layer model instead of repeatedly
searching for a global fixed point.

The implemented layers are:

```text
COPY
CONTROL
TEXT
TYPE
COLOR
ABILITY
PT_CDA
PT_SET
PT_MODIFY
PT_SWITCH
RULES
```

`COPY` and `TEXT` exist as explicit layer positions, but their presence does not
mean that full general copy or text-changing rules are implemented.

The engine uses:

- `StaticContinuousRule`,
- `ContinuousEffectDefinition`,
- `LayeredModifier`,
- stat-specific default layer assignment.

Examples of default classification:

- controller changes -> `CONTROL`,
- types/subtypes -> `TYPE`,
- abilities/keywords -> `ABILITY`,
- P/T setting -> `PT_SET`,
- P/T modification and counters -> `PT_MODIFY`,
- P/T switching -> `PT_SWITCH`.

P/T characteristic-defining effects can explicitly use `PT_CDA`.

---

# 24. Layer ordering

Effects are evaluated layer by layer.

Effects in the same layer use:

1. valid dependency ordering,
2. timestamp,
3. stable sequence order.

Effects created in the same logical step therefore still have deterministic
ordering.

A declared dependency is not a general preference mechanism. It should represent
a dependency that corresponds to the supported continuous-effect dependency
model.

Supported automatic dependency derivation currently covers relationships based
on:

- affected recipients,
- effect existence.

It does not perform arbitrary analysis of Python callbacks.

If dependency declarations form a cycle, the cyclic part is ignored and stable
timestamp ordering is used.

---

# 25. Layer recipient selection

A target specification sees the game state produced by earlier layers.

It does not see the final state after all later layers have already been applied.

If one continuous effect operates in multiple layers, it keeps the set of
recipients established when that effect begins applying.

This avoids unstable fixed-point behavior such as repeated oscillation from
effects conceptually similar to:

```text
Creatures with power 2 get +1/+1.
```

The engine no longer performs a blind repeated fixed-point search over the whole
continuous-effect system.

---

# 26. Replacement effects

Replacement effects are represented through:

- `ReplacementEffectDefinition`,
- runtime `ReplacementEffect`,
- `ReplacementResolver`.

A replacement definition separates:

```text
predicate
```

from:

```text
transform
```

## Predicate

```python
predicate(state, effect, operation)
```

must be a pure applicability test.

It must not consume runtime capacity or execute game operations.

## Transform

```python
transform(state, effect, operation)
```

runs only after that replacement effect has been chosen.

It returns a tuple of replacement operations.

An empty tuple means the original event is fully replaced with no resulting
operation.

The transform should not execute the resulting game operations directly.

The operation executor performs them afterward.

---

# 27. Replacement chaining

After one replacement effect modifies an operation, the resolver reevaluates the
resulting operation.

This allows one replacement to change properties such as:

- target,
- destination,
- damage amount,

and thereby make another replacement applicable.

The same replacement effect is protected from being repeatedly applied to the
same operation lineage where the rules model forbids that repetition.

---

# 28. Replacement choices

Controller hooks can participate in replacement selection.

Supported hooks include:

```text
choose_replacement(state, player, operation, effects)
accept_replacement(state, player, operation, effect)
order_replacement_events(state, player, operations)
```

The affected player, or controller of the affected permanent, makes the relevant
choice rather than automatically using the controller of the damage source.

Optional replacement effects can be declined.

Declining an optional effect does not consume its remaining uses.

Without a controller hook, the resolver uses deterministic fallback behavior.

---

# 29. Replacement priorities

Replacement effects may declare a category priority.

The supported ordering categories are:

```text
0  self-replacement
1  controller-changing entry effects
2  copy entry effects
3  face-down entry effects
4  ordinary replacement effects
```

These categories classify authored replacement definitions.

Their existence does not imply a complete implementation of all copy,
face-down or ETB rules from the Comprehensive Rules.

---

# 30. Prevention and replacement helpers

The engine includes helpers such as:

```python
prevent_damage(...)
damage_multiplier(...)
replace_zone(...)
```

Damage prevention can operate:

- per use,
- from a shared total prevention capacity.

A replacement effect may also have:

- limited uses,
- optional application,
- duration.

Static replacement definitions may be attached to card definitions.

Runtime replacement effects created by resolving a spell or ability can be
installed explicitly.

An installed effect may survive the source spell leaving unless it is explicitly
anchored to the incarnation of a permanent.

---

# 31. Zone-change replacement

Supported zone-change operations pass through the replacement resolver.

This allows interactions such as:

```text
would die -> exile instead
```

If a death is replaced by exile, the original dies event is not generated and
therefore does not cause dies triggers.

Token creation and supported aura-entry operations explicitly delegate relevant
entry operations through the replacement resolver.

The engine does not automatically decompose every arbitrary internal `.execute()`
callback into independently replaceable subevents.

---

# 32. Static abilities and continuous/replacement existence

Loss of static abilities is represented through:

```text
STAT_STATIC_ABILITIES
```

The layer evaluator and replacement registry take this into account.

An effect that has already begun applying in an earlier layer may continue where
required by the supported layer model.

The implementation covers the supported authored cases but is not a universal
model of every possible static-ability interaction in Magic.

---

# 33. Legend rule

The engine supports the legend rule for permanents controlled by the same player
with the same name.

The controller chooses one to keep.

The others are put into the graveyard even if they have indestructible.

Controllers can implement:

```python
choose_legend(state, player, cards)
```

Without such a hook, the first deterministic option is kept.

The current model relies on card name and the definition's legendary status.

---

# 34. Starter-deck rules slice

The engine currently maintains manually authored card definitions for the
supported Arena starter-deck experiment.

All five supported starter decklists can be loaded and played.

The supported card catalog is a test and research slice rather than a general
Magic card database.

Card definitions include authored information such as:

- mana cost,
- types,
- subtypes,
- power/toughness,
- keywords,
- rule-specific effect definitions.

Completing a match with these decks is not proof that every possible interaction
between supported mechanics is fully covered.

---

# 35. Explicitly unsupported or incomplete areas

The following areas are outside the current general implementation or remain
partial:

- arbitrary Oracle text parsing,
- complete Oracle card database,
- complete copy effects,
- complete text-changing effects,
- arbitrary CDA expressions,
- battles,
- protection,
- ward,
- banding,
- regeneration,
- command zone,
- emblems,
- general token copying,
- Fortifications,
- reconfigure,
- Auras attached to players or cards outside the battlefield,
- all temporary type-changing interactions,
- all name-changing interactions,
- all legendary-status-changing effects,
- full hypothetical ETB characteristic evaluation,
- all prevention distribution choices,
- arbitrary custom mutation rollback,
- unrestricted multiplayer elimination,
- Commander,
- team variants,
- extra turns,
- extra combats,
- arbitrary skip effects,
- general turn-ending effects,
- full format legality,
- all opening-hand special actions.

The engine is primarily targeted at two-player games.

---

# 36. Design rule for adding mechanics

A mechanic should not be considered supported merely because:

- an enum value exists,
- a keyword string exists,
- a phase name exists,
- one isolated code path recognizes it.

A mechanic should be considered part of the engine ruleset only when it has:

1. a runtime representation,
2. integration with the normal execution pipeline,
3. interaction with relevant state-based actions/events/triggers,
4. controller or console support where player choice is required,
5. integration tests covering its interaction with existing rules.

New mechanics should use the existing:

```text
Action
Effect
Operation
ResolutionEngine
EventBus
TriggerResolver
ReplacementResolver
SBAResolver
```

rather than bypassing them with direct state mutation.

Direct mutation is intended for setup/editor/test construction, not ordinary
gameplay effects.

---

# 37. Rules references

The implementation was designed with reference to the official Magic:
The Gathering Comprehensive Rules.

Important areas include:

```text
106–118   Mana and costs
302       Creatures
305       Lands
500–514   Turn structure
601–605   Casting, activating and mana abilities
608       Resolution
613       Continuous-effect interaction
614–616   Replacement/prevention interaction
702       Keyword abilities
704       State-based actions
```

Reference to a chapter means the engine follows relevant concepts from that
chapter for its supported mechanics.

It does **not** mean that every subrule in that chapter is implemented.

---

# 38. Summary

The current engine ruleset can be characterized as:

```text
manually authored cards
+ shared action/resolution pipeline
+ deterministic duel rules
+ real priority and stack
+ costs and mana solver
+ combat and major combat keywords
+ triggers and last-known information
+ state-based actions
+ tokens / attachments / planeswalkers
+ continuous-effect layers
+ replacement effects
```

The goal is a coherent rules engine for controlled experiments and AI evaluation,
not exhaustive emulation of every Magic rule or every printed card.
