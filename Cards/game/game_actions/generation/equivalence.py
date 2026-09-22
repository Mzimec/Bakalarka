"""Conservative symmetry reduction for one immutable generation pass.

Keys describe decision interchangeability, never runtime identity. Unknown rule
implementations and identity-observing state keep all concrete alternatives.
"""
from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Protocol
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum


class UnknownSemantics(Exception):
    pass


class StructuralKey:
    """Exact structured keys for the supported declarative rule vocabulary."""
    def __init__(self):
        from ..data_structs import ability, action_node
        from ...game_state.card import CardDefinition
        from ...game_state.modifier import TimeStamp
        from ...game_state.stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
        from ...stat_type import StatType
        from ...mana.mana_value import (ColoredSymbol, GenericSymbol, VariableSymbol, HybridGenericSymbol,
                                         PhyrexianSymbol, GeneralisedSymbol, DeterministicSymbol)
        from ..card_effects import TapSourceEffect, MoveSourceEffect, DamagePlayerEffect
        from ..mana_effects import AddManaEffect
        from ...target.target_resolver import TargetSlot, TargetResolver
        from ...target.target_selector import SingleTargetSelector, MinMaxTegetSelector
        from ...target.target_spec import QueryTargetSpec, PlayerQueryTargetSpec
        from helper.query_system.object_register import IndexKey
        from helper.query_system.query import EqQuery, InQuery, RangeQuery, AndQuery, OrQuery, DifferenceQuery, HasQuery
        self.records = {
            TimeStamp, CardDefinition, Stat, ModifiablePrimitiveStat, ModifiableReferenceStat, StatType,
            ColoredSymbol, GenericSymbol, VariableSymbol, HybridGenericSymbol, PhyrexianSymbol,
            GeneralisedSymbol, DeterministicSymbol, ability.CastSpellAbilityDefinition, ability.ActivatedAbilityDefinition,
            ability.ManaAbilityDefinition, ability.PlayLandAbilityDefinition,
            ability.SubAbilityDefinition, ability.SubAbilityVariable,
            action_node.EffectActionNode, action_node.AndActionNode, action_node.OrActionNode, action_node.ManaActionNode,
            TargetSlot, TargetResolver, SingleTargetSelector, MinMaxTegetSelector, QueryTargetSpec, PlayerQueryTargetSpec,
            IndexKey, EqQuery, InQuery, RangeQuery, AndQuery, OrQuery, DifferenceQuery, HasQuery,
        }
        self.effects = {TapSourceEffect, MoveSourceEffect, DamagePlayerEffect, AddManaEffect}
        self.supported = self.records | self.effects
        self.cache = {}

    def freeze(self, value):
        if value is None or type(value) in (bool, int, str, float, bytes):
            return (type(value), value)
        if isinstance(value, Enum):
            return (type(value), value)
        from ...game_state.player import Player
        if type(value) is Player:
            return (Player, id(value))
        if isinstance(value, Mapping):
            return ("mapping", frozenset((self.freeze(k), self.freeze(v)) for k, v in value.items()))
        if isinstance(value, Set):
            return ("set", frozenset(self.freeze(v) for v in value))
        if isinstance(value, (tuple, list)):
            return ("sequence", tuple(self.freeze(v) for v in value))
        if type(value) not in self.supported:
            raise UnknownSemantics(type(value).__name__)
        identity = id(value)
        if identity not in self.cache:
            members = ((field.name, getattr(value, field.name)) for field in fields(value)) if is_dataclass(value) else vars(value).items()
            self.cache[identity] = (value, (type(value), tuple((key, self.freeze(v)) for key, v in members)))
        return self.cache[identity][1]

    def ability(self, definition):
        # The local ability name only identifies an otherwise identical rule.
        if type(definition) not in self.records:
            raise UnknownSemantics(type(definition).__name__)
        return (type(definition), tuple((field.name, self.freeze(getattr(definition, field.name)))
                for field in fields(definition) if field.name != "key"))


class EquivalencePolicy(Protocol):
    def bind(self, state) -> EquivalenceContext: ...


@dataclass(frozen=True)
class ConservativeEquivalencePolicy:
    """Create a fresh context; no signature survives a decision/state mutation."""
    def bind(self, state):
        return EquivalenceContext(state)


class EquivalenceContext:
    def __init__(self, state):
        self.state = state
        self.structure = StructuralKey()
        self.cards = {}
        self._classes = {}
        self.enabled = False
        from ...game_state.state import State
        if not isinstance(state, State):
            return
        from helper.query_system.query import EqQuery
        from ...game_state.registers.card_register import IK_HAS_TRIGGERS, IK_STATIC_REPLACEMENT
        state.synchronise_registers()
        self.enabled = not (
            not state.stack.is_empty()
            or getattr(state.combat, "active", False)
            or getattr(state, "runtime_triggers", ())
            or getattr(state, "replacement_rules", ())
            or getattr(state, "_deferred_events", ())
            or state._cont_effect_manager.query()
            or state.query_cards(EqQuery(IK_HAS_TRIGGERS, True) | EqQuery(IK_STATIC_REPLACEMENT, True))
        )

    def card_key(self, card):
        from ...game_state.card import Card
        from ...enums import ZoneType
        from ...game_state.modifier import (
            ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
        )
        if not self.enabled or type(card) is not Card:
            return ("identity", id(card))
        identity = id(card)
        if identity in self.cards:
            return self.cards[identity]
        result = ("identity", identity)
        if (card.get_zone() in (ZoneType.HAND, ZoneType.BATTLEFIELD)
                and card.attached_to is None and not card.state.attached
                and not card.state.active_cont_effects and not card.state._anchored_modifiers
                and card.state.skip_untap is None
                and tuple(type(source) for source in card.modifier_sources) == (
                    ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
                )):
            try:
                # Keep all runtime metadata except identifiers and historical LKI.
                runtime = {k: v for k, v in vars(card).items() if k not in {
                    "_key", "runtime_id", "command_id", "_game_state", "_definition",
                    "_stats", "_modifier_sources", "_state", "_incarnation_history",
                }}
                values = {k: v for k, v in vars(card.state).items()
                          if k not in {"_on_change", "zone_info", "attach_info"}}
                zone_info = dict(vars(card.state.zone_info))
                from ...enums import CardType
                from ...rules.lands import BASIC_LAND_MANA
                plain_land = (
                    card.definition.types == frozenset({CardType.LAND})
                    and card.definition.subtypes
                    and all(subtype.name in BASIC_LAND_MANA for subtype in card.definition.subtypes)
                    and not card.definition.abilities
                    and not card.definition.triggers
                    and not card.definition.continuous_effects
                    and not card.definition.replacement_effects
                )
                if plain_land:
                    # Old basic lands remain interchangeable across turn numbers.
                    # Retain freshness: animating a newly controlled land could
                    # make summoning sickness relevant later in this turn.
                    runtime["controlled_since"] = card.controlled_since >= card.get_controller(self.state).last_turn_started
                    zone_info["entered_at"] = card.state.zone_info.entered_at.turn_number == self.state.turn.number
                result = (
                    "card", self.structure.freeze(card.definition), self.structure.freeze(card.stats),
                    self.structure.freeze(runtime), self.structure.freeze(values),
                    self.structure.freeze(zone_info),
                    self.structure.freeze(vars(card.state.attach_info)),
                    self.structure.freeze(tuple((key, card.get_stat(key, self.state)) for key in card.stats)),
                )
            except UnknownSemantics:
                pass
        if result[0] == "card":
            # Intern once: mana backtracking compares small class IDs rather
            # than repeatedly hashing the full characteristic/rule graph.
            result = ("card", self._classes.setdefault(result, len(self._classes)))
        self.cards[identity] = result
        return result

    def ability_key(self, ability):
        card = self.card_key(ability.source)
        if card[0] == "identity":
            return ("ability_identity", id(ability.source), ability.key, id(ability.definition))
        try:
            return (type(ability), card, id(ability.controller), self.structure.ability(ability.definition),
                    self.structure.freeze(ability.stats))
        except UnknownSemantics:
            return ("ability_identity", id(ability.source), ability.key, id(ability.definition))

    def representatives(self, abilities):
        seen = set()
        for ability in abilities:
            key = self.ability_key(ability)
            if key not in seen:
                seen.add(key)
                yield ability
