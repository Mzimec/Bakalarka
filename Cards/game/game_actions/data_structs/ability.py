"""Ability definitions, runtime bindings and subability composition."""

from __future__ import annotations
from typing import TYPE_CHECKING, override, overload, Any
from functools import cached_property
from dataclasses import dataclass, field, replace
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from immutabledict import immutabledict
from types import MappingProxyType
import copy

if TYPE_CHECKING:
    from ...game_state import Card, State, Player
    from .. import Effect
    from ..resolution.event_bus import GameEvent
    from .action_node import (
        ActionNode,
        AndActionNode,
    )
    from ...target import TargetSlot
    from ...target.target_resolver import ImmutableTargetBinding
    from ...mana.mana_value import ImmutableManaValue, ManaValue, ManaValueBase
    from ...ai.mana_solver import ManaSolver

from ...game_state.modifier import ModifierSource, ContinuouosEffectModifierSource
from .game_action import AbilityAction, ExecutionPlan, GameAction
from ...game_state.stat import (
    HasModifiableStats,
    ModifiableReferenceStat,
    ModifiablePrimitiveStat,
    Stat,
)
from ...stat_type import *
from helper.runtime_object import KeyedCollection
from ...enums import *
from ...target.target_resolver import RepetitionTargetSlotWrapper

__all__ = [
    "ZoneType",
    "EffectBinding",
    "EffectSequence",
    "SubAbilityDefinition",
    "AbilityDefinition",
    "Ability",
    "PriorityActionAbility",
    "CastSpellAbility",
    "ActivatedAbility",
    "ManaAbility",
    "TriggerCondition",
    "TriggerAbilityDefinition",
    "TriggerAbility",
]


@dataclass(frozen=True)
class EffectBinding:
    """!
    @brief Resolved connection between an effect object and the slots it consumes.

    @var effect
        The concrete `Effect` instance being applied.
    @var slots
        The set of target slots (as `RepetitionTargetSlotWrapper`) whose
        chosen targets feed into this effect when it resolves.
    """

    effect: Effect
    slots: frozenset[RepetitionTargetSlotWrapper]


@dataclass(frozen=True)
class EffectSequence:
    """!
    @brief An ordered, immutable sequence of resolved effect bindings.

    Represents the full set of effects (and the slots each one uses)
    that make up one concrete resolution of an ability.

    @var sequence
        Tuple of `EffectBinding` entries in resolution order.
    """

    sequence: tuple[EffectBinding, ...]

    def get_used_slots(self) -> frozenset[RepetitionTargetSlotWrapper]:
        """!
        @brief Collects every target slot referenced anywhere in this sequence.
        @return Frozenset of all slots used by any effect binding in the
                sequence.
        """
        res: set[RepetitionTargetSlotWrapper] = set()
        for eb in self.sequence:
            for s in eb.slots:
                res.add(s)
        return frozenset(res)

    def get_used_effects(self) -> frozenset[Effect]:
        """!
        @brief Collects every distinct effect object used in this sequence.
        @return Frozenset of the unique `Effect` instances bound in the
                sequence (duplicates from repeated bindings are
                collapsed).
        """
        return frozenset({eb.effect for eb in self.sequence})


@dataclass(frozen=True)
class SubAbilityVariable:
    """!
    @brief Metadata describing an X/Y-style variable cost or effect magnitude.

    Links a variable (e.g. the X in "Fireball deals X damage") to the
    cost and action sub-definitions that scale with its chosen value,
    plus bounds on the legal value range.

    @var var_type
        Which kind of scalable variable this is (e.g. cost-X vs
        effect-Y), as an `SAVariableType`.
    @var cost_subdef
        The `SubAbilityDefinition` describing how the cost scales with
        the chosen value.
    @var action_subdef
        The `SubAbilityDefinition` describing how the effect scales with
        the chosen value.
    @var min_value
        Smallest legal value for the variable. Defaults to 0.
    @var max_cap
        Optional explicit upper bound. If `None`, a fallback bound is
        used by `get_max_value`.
    """

    var_type: SAVariableType
    cost_subdef: SubAbilityDefinition
    action_subdef: SubAbilityDefinition
    min_value: int = 0
    max_cap: int | None = None

    def get_max_value(self, state: State, subability: RuntimeSubAbility) -> int:
        """!
        @brief Computes the largest legal value this variable may take.

        @param state Current game state (unused by the default
               implementation, but kept for future state-dependent caps,
               e.g. based on available mana).
        @param subability The compiled `RuntimeSubAbility` this variable
               belongs to (currently unused, same reasoning as `state`).
        @return The effective maximum value: `max_cap` if it is a valid
                integer no smaller than `min_value`, otherwise a safe
                default of 20 (clamped to be at least `min_value`).
        """
        # X/Y choices are optional metadata on a card definition.  A missing
        # specialised resolver must not crash action generation; use a bounded
        # deterministic fallback and honour an explicit cap.
        cap = self.max_cap if self.max_cap is not None else 20
        if type(cap) is not int or cap < self.min_value:
            cap = self.min_value
        return max(self.min_value, cap)


@dataclass(frozen=True)
class SubAbilityDefinition:
    """!
    @brief Defines either the cost part or the effect part of an ability.

    An `AbilityDefinition` is built from one or more cost sub-defs and
    one or more action (effect) sub-defs; a `SubAbilityComposer` merges
    them into a single compiled `RuntimeSubAbility`.

    @var action_node
        Root of the action-tree fragment (costs to pay or effects to
        apply) contributed by this sub-definition, or `None` if it
        contributes no actions of its own (e.g. a pure mana-cost
        sub-def).
    @var mana_cost
        Optional reference to a modifiable mana-cost stat, resolved
        against the owning `Ability` when compiling the total mana cost.
    @var slots
        Target slots introduced by this sub-definition's action node.
    @var effects
        Effect objects referenced by this sub-definition's action node.
    @var resolution_speed
        Optional override of how fast this sub-ability resolves (e.g.
        instant vs sorcery speed); all sub-defs combined into one
        ability must agree on this value.
    """

    action_node: ActionNode | None = None
    mana_cost: ModifiableReferenceStat[ManaValue, ImmutableManaValue] | None = None
    slots: frozenset[TargetSlot] = field(default_factory=frozenset)
    effects: frozenset[Effect] = field(default_factory=frozenset)
    resolution_speed: ResolutionSpeed | None = None


class SubAbilityComposer:
    """!
    @brief Accumulates and compiles a multiset of `SubAbilityDefinition`s.

    Card abilities can be built up piecewise (e.g. a base cost plus
    additional costs from other effects, or a base effect plus bonus
    effects). This composer tracks how many times each distinct
    `SubAbilityDefinition` has been added, validates that all added
    definitions agree on resolution speed, and can `compile` the
    accumulated definitions into a single `RuntimeSubAbility` for a
    specific `Ability` instance and game state.
    """

    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, subability: SubAbilityComposer) -> None: ...
    @overload
    def __init__(self, subdefs: Iterable[SubAbilityDefinition]) -> None: ...
    def __init__(
        self, first: SubAbilityComposer | Iterable[SubAbilityDefinition] | None = None
    ) -> None:
        """!
        @brief Creates an empty composer, a copy of another, or one seeded from sub-defs.

        @param first Either another `SubAbilityComposer` to copy (its
               internal counts and resolution-speed bookkeeping are
               copied), an iterable of `SubAbilityDefinition`s to seed
               this composer with, or `None` to start empty.
        """
        if isinstance(first, SubAbilityComposer):
            # Copy constructor: duplicate internal state rather than
            # sharing the same mutable dict.
            self._subdefs: dict[SubAbilityDefinition, int] = dict(first._subdefs)
            self._resolution_speed: ResolutionSpeed | None = first._resolution_speed
            self._speed_count: int = first._speed_count

        else:
            self._subdefs = {}
            self._resolution_speed = None
            self._speed_count = 0
            if first:
                self.extend_subdefs(first)

        # Read-only view exposed for external inspection without
        # allowing callers to mutate the internal dict directly.
        self._subdefs_proxy: MappingProxyType[SubAbilityDefinition, int] = MappingProxyType(
            self._subdefs
        )

    def extend_subdefs(self, subdefs: Iterable[SubAbilityDefinition]) -> None:
        """!
        @brief Adds sub-definitions to the composer, incrementing their counts.

        Validates that any sub-definition carrying an explicit
        `resolution_speed` agrees with the speed already established by
        previously added sub-definitions.

        @param subdefs Sub-definitions to add (multiplicities are summed
               if the same definition is added more than once).
        @throws ValueError If a sub-definition's `resolution_speed`
                conflicts with one already recorded on this composer.
        """
        for subdef in subdefs:
            if (
                self._resolution_speed is not None
                and self._resolution_speed != subdef.resolution_speed
            ):
                raise ValueError(
                    "  SubAbilityDefinitions with different resolution speeds cannot be combined."
                )

            if subdef.resolution_speed:
                self._speed_count += 1
                if not self._resolution_speed:
                    self._resolution_speed = subdef.resolution_speed

            self._subdefs[subdef] = self._subdefs.get(subdef, 0) + 1

    def remove_subdefs(self, subdefs: Iterable[SubAbilityDefinition]) -> None:
        """!
        @brief Removes previously added sub-definitions, decrementing their counts.

        Entries whose count drops to zero (or below) are deleted
        entirely. If no sub-definition with an explicit resolution speed
        remains, the composer's recorded `resolution_speed` is reset to
        `None` so a future, differently-timed sub-def can be added.

        @param subdefs Sub-definitions to remove. Sub-definitions not
               currently present are silently ignored.
        """
        for subdef in subdefs:
            if subdef not in self._subdefs:
                continue

            if subdef.resolution_speed:
                self._speed_count -= 1

            self._subdefs[subdef] -= 1

            if self._subdefs[subdef] <= 0:
                del self._subdefs[subdef]

        if self._speed_count <= 0:
            self._resolution_speed = None
            self._speed_count = 0

    def _compile_mana_cost(self, ability: Ability, state: State) -> ImmutableManaValue | None:
        """!
        @brief Sums the mana costs contributed by every accumulated sub-definition.

        For each distinct sub-definition with a `mana_cost`, resolves it
        either by parsing a literal string/`ManaValueBase`, or by
        reading the corresponding stat off `ability` (which allows
        continuous effects/modifiers to alter the cost). The resolved
        amount is then added once per recorded multiplicity.

        @param ability The `Ability` instance whose stats are consulted
               when a sub-def's `mana_cost` is a stat reference rather
               than a literal.
        @param state Current game state, passed through to stat
               resolution.
        @return The combined `ImmutableManaValue` across all
                sub-definitions, or `None` if no sub-definition
                contributed a mana cost.
        """
        from ...mana.mana_value import ManaValue, ImmutableManaValue, ManaValueBase, parse_mana_cost

        accumulated_mana: ManaValue | None = None
        for k, v in self._subdefs.items():
            if k.mana_cost is None:
                continue
            smc: ImmutableManaValue | None = None
            if isinstance(k.mana_cost, (str, ManaValueBase)):
                # Literal mana cost (e.g. "{2}{R}") — parse directly.
                smc = parse_mana_cost(k.mana_cost)
            elif k.mana_cost:
                # Stat-based mana cost — resolve through the ability so
                # continuous effects/modifiers are taken into account.
                smc = ability.get_stat(k.mana_cost.stat_type, state)

                if not smc:
                    continue

            for _ in range(v):
                if accumulated_mana is None:
                    accumulated_mana = ManaValue()
                accumulated_mana.add(smc)

        return ImmutableManaValue(accumulated_mana) if accumulated_mana is not None else None

    def _compile_action_graph(
        self,
    ) -> tuple[
        immutabledict[str, RepetitionTargetSlotWrapper],
        immutabledict[str, Effect],
        ActionNode | None,
    ]:
        """!
        @brief Merges every accumulated sub-definition's action tree into one graph.

        Each occurrence of a sub-definition (respecting its recorded
        multiplicity) gets its own copy of the action node and target
        slots, disambiguated with a numeric suffix (`_0`, `_1`, ...) so
        that repeated identical sub-definitions don't collide on slot
        keys. All resulting root nodes are then combined: zero children
        yields `None`, one child is returned as-is, and multiple
        children are wrapped in an `AndActionNode` so they all execute
        together.

        @return A 3-tuple of:
                - the merged slot map (suffixed slot key ->
                  `RepetitionTargetSlotWrapper`),
                - the merged effect map (effect key -> `Effect`),
                - the combined root `ActionNode`, or `None` if no
                  sub-definition contributed an action node.
        """
        from .action_node import AndActionNode

        slots: dict[str, RepetitionTargetSlotWrapper] = {}
        effects: dict[str, Effect] = {}
        children: list[ActionNode] = []

        i: int = 0
        for subdef, count in self._subdefs.items():
            if not subdef.action_node:
                continue

            for effect in subdef.effects:
                effects[effect.key] = effect

            for _ in range(count):
                # Each repetition of the same sub-definition gets a
                # unique suffix so its slots/nodes don't collide with
                # other repetitions (e.g. "add mana" used twice).
                sufix: str = f"_{i}"
                i += 1
                children.append(subdef.action_node.create_with_sufix(sufix))

                for slot in subdef.slots:
                    slots[slot.key + sufix] = RepetitionTargetSlotWrapper(slot.key + sufix, slot)

        action_node: ActionNode | None
        if not children:
            action_node = None
        elif len(children) == 1:
            action_node = children[0]
        else:
            action_node = AndActionNode(children)

        return immutabledict(slots), immutabledict(effects), action_node

    def compile(self, ability: Ability, state: State) -> RuntimeSubAbility | None:
        """!
        @brief Compiles all accumulated sub-definitions into a single runtime ability.

        @param ability The `Ability` the compiled mana cost should be
               resolved against.
        @param state Current game state, used for stat/mana-cost
               resolution.
        @return A `RuntimeSubAbility` combining the merged action graph,
                total mana cost, slots, effects, and resolution speed; or
                `None` if no sub-definitions have been accumulated at
                all.
        """
        if not self._subdefs:
            return None

        compiled_mana_cost: ImmutableManaValue | None = self._compile_mana_cost(ability, state)
        compiled_slots: immutabledict[str, RepetitionTargetSlotWrapper]
        compiled_effects: immutabledict[str, Effect]
        compiled_action_node: ActionNode | None

        compiled_slots, compiled_effects, compiled_action_node = self._compile_action_graph()

        return RuntimeSubAbility(
            action_node=compiled_action_node,
            mana_cost=compiled_mana_cost,
            slots=compiled_slots,
            effects=compiled_effects,
            resolution_speed=self._resolution_speed,
        )


@dataclass(frozen=True)
class RuntimeSubAbility:
    """!
    @brief Fully compiled cost or effect half of an ability, ready to generate actions.

    Produced by `SubAbilityComposer.compile`. Bundles the merged action
    tree, resolved mana cost, and lookup tables for slots/effects so
    that generated action-option dictionaries (mapping effect keys to
    slot keys) can be turned into concrete `EffectSequence`s.

    @var action_node
        Root of the combined action tree (costs or effects), or `None`
        if this sub-ability contributes no actions.
    @var mana_cost
        Total resolved mana cost, or `None` if none applies.
    @var slots
        Map from (possibly suffixed) slot key to its
        `RepetitionTargetSlotWrapper`.
    @var effects
        Map from effect key to its concrete `Effect` object.
    @var resolution_speed
        The resolution speed shared by all merged sub-definitions, if
        any specified one.
    """

    action_node: ActionNode | None
    mana_cost: ImmutableManaValue | None = None
    slots: immutabledict[str, RepetitionTargetSlotWrapper] = field(default_factory=immutabledict)
    effects: immutabledict[str, Effect] = field(default_factory=immutabledict)
    resolution_speed: ResolutionSpeed | None = None

    def normalize_effect_map(self, map: Mapping[str, Iterable[str]]) -> EffectSequence:
        """!
        @brief Convert effect keys from the action tree into concrete effect objects.
        @param map Mapping from effect keys to target slot keys.
        @return Immutable sequence of concrete effect bindings.
        @throws KeyError If an effect key in `map` is not known to this
                sub-ability, or if a referenced slot key has no
                corresponding `TargetSlot`.
        """
        bindings: list[EffectBinding] = []
        for k, v in map.items():
            if k not in self.effects:
                raise KeyError(k)

            slots: set[RepetitionTargetSlotWrapper] = set()

            for key in v:
                slot = self.slots.get(key)

                if slot is None:
                    raise KeyError(f"  No TargetSlot for '{key}' in self.slots.")

                slots.add(slot)

            bindings.append(EffectBinding(effect=self.effects[k], slots=frozenset(slots)))

        return EffectSequence(sequence=tuple(bindings))

    def get_used_slots_in_esmap(
        self, effects_to_slots: Mapping[str, Iterable[str]]
    ) -> set[RepetitionTargetSlotWrapper]:
        """!
        @brief Collect every target slot referenced by a selected effect mapping.
        @param effects_to_slots Mapping selected for one generated action option.
        @return Target slots required by the selected effects.
        """
        keys: set[str] = set()
        for slot_keys in effects_to_slots.values():
            keys.update(slot_keys)

        res: set[RepetitionTargetSlotWrapper] = set()
        for k in keys:
            if k in self.slots:
                res.add(self.slots[k])

        return res

    def get_used_slots_in_tbinding(
        self, binding: Mapping[str, Mapping[str, Any]]
    ) -> set[RepetitionTargetSlotWrapper]:
        """!
        @brief Collect every target slot referenced by a resolved target binding.

        Same idea as `get_used_slots_in_esmap`, but reads slot keys from
        an already-resolved target binding structure (slot-group key ->
        {target-slot-key: value}) rather than from an effect-to-slots
        mapping.

        @param binding Resolved binding mapping, as produced during
               action generation/target resolution.
        @return Target slots referenced anywhere in `binding` that are
                known to this sub-ability.
        """
        res: set[RepetitionTargetSlotWrapper] = set()
        for v in binding.values():
            for k in v.keys():
                if k in self.slots:
                    res.add(self.slots[k])
        return res


@dataclass(frozen=True)
class AbilityDefinition:
    """!
    @brief Static data that describes how a card ability can be used.

    This is the card-authoring-level description of an ability (shared
    across all instances of a card), as opposed to `Ability`, which is
    the runtime object bound to one specific source card and controller.

    @var uses_stack
        Whether activating/casting this ability puts it on the stack.
        Defaults to `True`; mana abilities and some special/loyalty
        actions override this.
    @var subdefs
        Map from `SAVariableType` to `SubAbilityVariable`, describing any
        X/Y-style scalable costs or effects this ability has.
    @var allowed_zones
        Zones from which this ability may be activated. Defaults to
        battlefield-only.
    @var key
        Unique string identifier for this ability definition on its
        card.
    @var cost_subdefs
        Sub-definitions describing the cost(s) of this ability.
    @var action_subdefs
        Sub-definitions describing the effect(s) of this ability.
    @var is_spell
        Whether this ability represents casting the card itself as a
        spell (as opposed to an activated/triggered ability).
    @var sorcery_speed
        Whether this ability may only be activated at sorcery speed,
        regardless of its other properties.
    @var is_mana_ability
        Whether this is a mana ability (doesn't use the stack, can't be
        responded to, and is excluded from speculative action
        generation elsewhere in the engine).
    @var loyalty_cost
        Loyalty cost for planeswalker abilities, or `None` if this isn't
        a loyalty ability.
    """

    uses_stack: bool = True
    subdefs: immutabledict[SAVariableType, SubAbilityVariable] = field(
        default_factory=immutabledict
    )
    allowed_zones: frozenset[ZoneType] = field(
        default_factory=lambda: frozenset({ZoneType.BATTLEFIELD})
    )
    key: str = "ability"
    cost_subdefs: tuple[SubAbilityDefinition, ...] = ()
    action_subdefs: tuple[SubAbilityDefinition, ...] = ()
    is_spell: bool = False
    sorcery_speed: bool = False
    is_mana_ability: bool = False
    loyalty_cost: int | None = None

    def validation_error(self, source: Card, controller: Player, state: State) -> str | None:
        """!
        @brief Return a validation message when the requested action is not legal.

        Checks, in order: loyalty-ability-specific rules (integer cost,
        must use the stack, must be on the battlefield, once-per-turn
        limit, sufficient loyalty counters), that a spell has a mana
        cost and isn't a land, that the ability is usable from the
        source's current zone, that `controller` actually controls (or
        owns, for spells) the ability, and finally timing restrictions
        for sorcery-speed abilities (active player, main phase, empty
        stack).

        @param source The card the ability originates from.
        @param controller The player attempting to use the ability.
        @param state Current game state.
        @return `None` if the action is legal, otherwise a short
                human-readable string explaining why it is not.
        """
        if self.loyalty_cost is not None:
            if type(self.loyalty_cost) is not int:
                return "Loyalty cost must be an integer."
            if self.is_spell or self.is_mana_ability or not self.uses_stack:
                return "Loyalty abilities must be activated abilities using the stack."
            if source.get_zone() != ZoneType.BATTLEFIELD:
                return "Loyalty abilities require a battlefield permanent."
            if source.loyalty_activated_turn == state.turn.number:
                return "A loyalty ability of this permanent was already activated this turn."
            if source.state.counters.get(CounterType.LOYALTY, 0) + self.loyalty_cost < 0:
                return "Insufficient loyalty counters."
        if self.is_spell and source.get_mana_cost(state) is None:
            return "A spell without a mana cost cannot be cast by paying its mana cost."
        if self.is_spell and CardType.LAND in source.get_types(state):
            return "Lands are played as special actions, not cast as spells."
        if not self.is_usable_in_zone(source.get_zone()):
            return f"Ability '{self.key}' is not available in {source.get_zone().name}."
        permitted_player = source.owner if self.is_spell else source.get_controller(state)
        if controller is not permitted_player:
            return "You do not control this ability."
        # Determine whether the ability is restricted to sorcery speed:
        # explicit sorcery_speed flag, loyalty abilities (always sorcery
        # speed), or a spell that is neither an instant nor flash.
        sorcery_speed = (
            self.loyalty_cost is not None
            or self.sorcery_speed
            or (
                self.is_spell
                and CardType.INSTANT not in source.get_types(state)
                and not source.has_keyword(state, "flash")
            )
        )
        if sorcery_speed and (
            state.active_player is not controller
            or state.turn.phase not in {TurnPhase.PRECOMBAT_MAIN, TurnPhase.POSTCOMBAT_MAIN}
            or not state.stack.is_empty()
        ):
            return "Use this ability during your main phase with an empty stack."
        return None

    def is_usable_in_zone(self, zone: ZoneType) -> bool:
        """!
        @brief Return whether the ability is available from the given card zone.
        @param zone Zone to check.
        @return True if the ability is available in the zone.
        """
        return zone in self.allowed_zones

    def to_ability(self, card: Card, player: Player) -> Ability:
        """!
        @brief Binds this static definition to a specific source card and controller.
        @param card The card this ability instance originates from.
        @param player The player who would control/cast this ability
               instance.
        @return A new runtime `Ability` wrapping this definition.
        """
        ability_type = (CastSpellAbility if self.is_spell else
                        ManaAbility if self.is_mana_ability else ActivatedAbility)
        return ability_type(self, card, player)


@dataclass(frozen=True)
class AbilityData:
    """!
    @brief Immutable payload backing a runtime `Ability` instance.

    Kept as a small separate dataclass so `Ability` (which mixes in
    `HasModifiableStats`) can cheaply share/copy its identity data (via
    the copy-constructor overload) without re-deriving derived state.

    @var definition
        The static `AbilityDefinition` this instance is based on.
    @var source
        The card this ability instance is bound to.
    @var caster
        The player controlling/casting this ability instance.
    """

    definition: AbilityDefinition
    source: Card
    caster: Player


class Ability(HasModifiableStats):
    """!
    @brief Runtime ability bound to a specific source card.

    Wraps a static `AbilityDefinition` together with the specific card
    (`source`) and player (`caster`) it applies to for one particular
    instance of use. Exposes modifiable stats (e.g. mana cost) so
    continuous effects can alter the ability's cost, and can produce a
    concrete `GameAction` once cost and effect generators have been
    chosen.
    """

    @overload
    def __init__(self, ability: Ability) -> None: ...
    @overload
    def __init__(self, definition: AbilityDefinition, source: Card, caster: Player) -> None: ...
    def __init__(
        self,
        first: Ability | AbilityDefinition,
        second: Card | None = None,
        third: Player | None = None,
    ) -> None:
        """!
        @brief Creates a new ability instance, or copies an existing one.

        @param first Either an existing `Ability` to copy (shares its
               underlying `AbilityData`, stats, and modifier sources), or
               an `AbilityDefinition` to bind to `second`/`third`.
        @param second The source `Card`, required when `first` is an
               `AbilityDefinition`.
        @param third The controlling/casting `Player`, required when
               `first` is an `AbilityDefinition`.
        @throws ValueError If `first` is an `AbilityDefinition` but
                `second` or `third` is not provided.
        @throws KeyError If two sub-definitions' mana-cost stats collide
                on the same `StatType` while building this ability's stat
                table.
        """
        if isinstance(first, Ability):
            # Copy constructor: reuse the same immutable backing data
            # rather than rebuilding stats from scratch.
            self._data: AbilityData = first._data
            self._stats = first._stats
            self._modifier_sources = first._modifier_sources

        else:
            if second is None or third is None:
                raise ValueError("Both 'source' and 'caster' must be provided for a new Ability.")

            self._data = AbilityData(first, second, third)

            stats_builder: dict[StatType, Stat] = {
                STAT_CONTROLLER: ModifiablePrimitiveStat(third, STAT_CONTROLLER)
            }

            # Register every mana-cost stat contributed either by the
            # X/Y variable sub-defs or directly by cost/action
            # sub-defs, so continuous effects can find and modify them
            # by StatType.
            for param in first.subdefs.values():
                if param.cost_subdef and hasattr(param.cost_subdef.mana_cost, "stat_type"):
                    if param.cost_subdef.mana_cost.stat_type in stats_builder:
                        raise KeyError("  Duplicity in keys in stats dict.")
                    stats_builder[param.cost_subdef.mana_cost.stat_type] = (
                        param.cost_subdef.mana_cost
                    )
            for subdef in (*first.cost_subdefs, *first.action_subdefs):
                if hasattr(subdef.mana_cost, "stat_type"):
                    stats_builder[subdef.mana_cost.stat_type] = subdef.mana_cost

            self._stats: immutabledict[StatType, Stat] = immutabledict(stats_builder)

            self._modifier_sources: tuple[ModifierSource, ...] = second.modifier_sources

    # HasStat properties
    @property
    @override
    def stats(self):
        """!
        @brief The modifiable stat table for this ability (e.g. mana-cost stats).
        @return Immutable mapping of `StatType` to `Stat`.
        """
        return self._stats

    # HasModifiers properties
    @property
    @override
    def modifier_sources(self):
        """!
        @brief Modifier sources inherited from this ability's source card.
        @return Tuple of `ModifierSource` affecting this ability's stats.
        """
        return self._modifier_sources

    @property
    def definition(self) -> AbilityDefinition:
        """!
        @brief The static `AbilityDefinition` this instance is based on.
        """
        return self._data.definition

    @property
    def key(self) -> str:
        """!
        @brief Shortcut for `self.definition.key`.
        """
        return self.definition.key

    @property
    def source(self) -> Card:
        """!
        @brief The card this ability instance is bound to.
        """
        return self._data.source

    @property
    def controller(self) -> Player:
        """!
        @brief The player controlling/casting this ability instance.
        """
        return self._data.caster

    def to_game_action(self, c_generator: ExecutionPlan, a_generator: ExecutionPlan) -> GameAction:
        """!
        @brief Wrap chosen cost and effect generators into an executable game action.
        @param c_generator Generator for the cost operations.
        @param a_generator Generator for the ability effect operations.
        @return Game action representing one concrete ability choice.
        """
        from .game_action import ManaAbilityAction

        # Mana abilities are represented by a distinct action subclass
        # (`ManaAbilityAction`) so the rest of the engine can special-case
        # them (e.g. never putting them on the stack).
        action_type = ManaAbilityAction if self.definition.is_mana_ability else AbilityAction
        return action_type(
            action_key=self.key,
            source=self._data.source,
            cost_generator=c_generator,
            action_generator=a_generator,
            uses_stack=self.definition.uses_stack and not self.definition.is_mana_ability,
            controller=self.controller,
            ability=self.definition,
        )

    def is_activatable(self) -> bool:
        """!
        @brief Quick zone-only check of whether this ability could currently be used.

        Note this only checks zone legality (via
        `AbilityDefinition.is_usable_in_zone`); full legality (control,
        timing, loyalty limits, etc.) is determined by
        `AbilityDefinition.validation_error`.

        @return `True` if the source card's current zone permits this
                ability.
        """
        return self.definition.is_usable_in_zone(self._data.source.get_zone())


class PriorityActionAbility(Ability):
    """An ability offered while its controller has priority."""


class CastSpellAbility(PriorityActionAbility):
    """Runtime binding for casting a card as a spell."""


class ActivatedAbility(PriorityActionAbility):
    """Runtime binding for an activated card ability."""


class ManaAbility(ActivatedAbility):
    """An activation also available while planning a mana payment."""


class TriggerCondition(ABC):
    """!
    @brief Base predicate used to decide whether an event should trigger an ability.
    """

    # None preserves generic event conditions; zone-aware conditions explicitly
    # distinguish leaves-the-battlefield from "from anywhere" triggers.
    looks_back_in_time: bool | None = None

    @abstractmethod
    def matches(self, state: State, event: GameEvent) -> bool:
        """!
        @brief Return true when the event satisfies this trigger condition.
        @param state Current game state.
        @param event Event being tested.
        @return True if this condition is satisfied.
        """
        pass


@dataclass(frozen=True)
class TriggerAbilityDefinition(AbilityDefinition):
    """!
    @brief Static data for an ability that is produced by a matching game event.

    Extends `AbilityDefinition` with the `TriggerCondition` that decides
    whether a given `GameEvent` causes this ability to trigger, plus an
    optional "intervening if" clause re-checked at resolution time.

    @var condition
        The `TriggerCondition` used to match incoming game events.
        Defaults to `None` (never triggers) if not set.
    @var intervening_if
        Optional callable `(state, event) -> bool` re-checked when the
        trigger would fire/resolve (an "intervening if" clause), on top
        of the base `condition` match.
    """

    condition: TriggerCondition = field(default=None)
    intervening_if: Any | None = None

    def validation_error(self, source, controller, state):
        # Eligibility was captured when the event occurred, before stack resolution.
        """!
        @brief Return a validation message when the requested action is not legal.

        Triggered abilities are always legal to put on the stack once
        triggered — their eligibility was already determined by the
        triggering event, not by re-checking normal activation rules at
        resolution time.

        @param source Unused; kept for signature compatibility with
               `AbilityDefinition.validation_error`.
        @param controller Unused; kept for signature compatibility.
        @param state Unused; kept for signature compatibility.
        @return Always `None` (never blocks a triggered ability).
        """
        return None

    @override
    def to_ability(self, card, player):
        """!
        @brief Binds this trigger definition to a source card and controller.
        @param card The card this triggered ability originates from.
        @param player The player who will control this triggered
               ability instance.
        @return A new `TriggerAbility` (without an associated event yet;
                use its constructor directly to attach one).
        """
        return TriggerAbility(self, card, player)


class TriggerAbility(Ability):
    """!
    @brief Runtime triggered ability paired with the event that caused it.

    Extends `Ability` by carrying the specific `GameEvent` that (may
    have) triggered it, plus a snapshot of the source card's
    last-known state/characteristics (`source_last_known`) for triggers
    that need to reference information as it was at the moment of
    triggering (e.g. a creature that has since left the battlefield).
    """

    def __init__(
        self,
        definition: TriggerAbilityDefinition,
        source: Card,
        caster: Player,
        event: GameEvent | None = None,
        source_last_known: Any | None = None,
    ) -> None:
        """!
        @brief Creates a triggered ability instance, optionally bound to an event.

        @param definition The `TriggerAbilityDefinition` this instance is
               based on.
        @param source The card this triggered ability originates from.
        @param caster The player controlling this triggered ability.
        @param event The `GameEvent` that caused (or is being tested
               against) this trigger, or `None` if not yet bound to an
               event.
        @param source_last_known Optional snapshot of the source card's
               characteristics at the time of triggering, for use by
               triggers that care about "last known information".
        """
        super().__init__(definition, source, caster)
        self.event = event
        self.source_last_known = source_last_known
        # Capture identity now; later resolution must not treat a returned
        # card as the permanent that originally triggered this ability.
        self.source_revision = getattr(source, "zone_revision", None)

    def to_game_action(self, c_generator, a_generator):
        """!
        @brief Builds the game action for this trigger, attaching its triggering event.
        @param c_generator Generator for the cost operations.
        @param a_generator Generator for the ability effect operations.
        @return The `GameAction` from `Ability.to_game_action`, with
                `trigger_event` set to this instance's `event`.
        """
        return replace(
            super().to_game_action(c_generator, a_generator),
            trigger_event=self.event,
            trigger_registration=getattr(self, "registration", None),
            trigger_source_revision=self.source_revision,
            trigger_source_last_known=self.source_last_known,
        )

    def matches(self, state: State) -> bool:
        """!
        @brief Evaluate the stored trigger event against the ability condition.

        Returns `False` early if there is no stored event, the
        definition isn't actually a `TriggerAbilityDefinition`, or no
        `condition` is configured. If an `intervening_if` clause is set,
        it must also currently hold. If the condition object exposes a
        `matches_trigger(self, state)` hook (for conditions that need
        access to the full trigger instance, e.g. `source_last_known`),
        that is preferred over the plain `matches(state, event)` check.

        @param state Current game state.
        @return True if the stored event matches the trigger condition.
        """
        if self.event is None or not isinstance(self.definition, TriggerAbilityDefinition):
            return False
        if self.definition.condition is None:
            return False
        if self.definition.intervening_if is not None and not self.definition.intervening_if(
            state, self.event
        ):
            return False
        if hasattr(self.definition.condition, "matches_trigger"):
            return self.definition.condition.matches_trigger(self, state)
        return self.definition.condition.matches(state, self.event)


class AbilityCollection(KeyedCollection[AbilityDefinition]):
    """!
    @brief Keyed collection of `AbilityDefinition`s belonging to a card.

    Thin specialization of `KeyedCollection` (looked up by each
    definition's `key`) with no additional behavior of its own.
    """

    pass