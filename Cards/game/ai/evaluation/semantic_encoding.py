"""Versioned, data-only rule semantics; never execute effects or predicates.

Only reviewed exact native types are interpreted. Unsupported rules remain
explicit unknown nodes. This is a model representation, never a rules engine
or an information-set identity. Published definitions may be cached; runtimes
and bound definitions may not enter this cache.
"""
from collections import Counter, OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, asdict
from enum import Enum
from math import isfinite

from helper.static_definition import is_static_definition, RuntimeBinding

SEMANTIC_VERSION = 1
_projection = ContextVar('semantic_projection', default=None)


@dataclass(frozen=True, slots=True)
class SemanticNode:
    """Immutable semantic AST with ordered children and no runtime references."""
    kind: str
    attributes: tuple = ()
    children: tuple = ()
    schema_version: int = SEMANTIC_VERSION

    def to_dict(self):
        return asdict(self)

    @property
    def unknowns(self):
        counts = Counter()
        stack = [self]
        while stack:
            node = stack.pop()
            if node.kind == 'unknown':
                counts.update([dict(node.attributes).get('reason', 'unsupported')])
            stack.extend(node.children)
        return dict(sorted(counts.items()))


def node(kind, children=(), **attributes):
    values = tuple(sorted(attributes.items()))
    if any(type(v) not in (str, int, float, bool, type(None))
           or (type(v) is float and not isfinite(v)) for _, v in values):
        raise ValueError('Semantic attributes must be finite detached scalars')
    return SemanticNode(kind, values, tuple(children))


def unknown(reason):
    return node('unknown', reason=reason)


def projected_semantics(value):
    """Optional projection hook, evaluated only after native visibility checks."""
    encoder = _projection.get()
    if encoder is None:
        return None
    if not is_static_definition(value) or isinstance(value, RuntimeBinding):
        return unknown('runtime_bound_definition').to_dict()
    return encoder.encode(value).to_dict()


class CardSemanticEncoder:
    """Own bounded immutable definition facts, independently of learned weights."""
    def __init__(self, capacity=512):
        if type(capacity) is not int or capacity < 0:
            raise ValueError('Semantic cache capacity must be nonnegative')
        self.capacity, self.cache, self.counts = capacity, OrderedDict(), Counter()
        self._active = set()

    @contextmanager
    def projecting(self):
        token = _projection.set(self)
        try:
            yield
        finally:
            _projection.reset(token)

    def encode(self, definition):
        if isinstance(definition, RuntimeBinding) or not is_static_definition(definition):
            raise TypeError('Semantic encoder requires a published context-free definition')
        identity = id(definition)
        entry = self.cache.get(identity)
        if entry is not None and entry[0] is definition:
            self.cache.move_to_end(identity)
            self.counts['hits'] += 1
            return entry[1]
        if identity in self._active or len(self._active) >= 64:
            return unknown('recursive_definition')
        self._active.add(identity)
        try:
            result = self._encode(definition)
        finally:
            self._active.remove(identity)
        self.counts['builds'] += 1
        if self.capacity:
            self.cache[identity] = definition, result
            while len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
                self.counts['evictions'] += 1
        return result

    def _encode(self, value):
        from game.game_state.card import CardDefinition
        from game.game_actions.data_structs.ability import (
            AbilityDefinition, PriorityActionAbilityDefinition, ActivatedAbilityDefinition,
            CastSpellAbilityDefinition, ManaAbilityDefinition, PlayLandAbilityDefinition,
            TriggerAbilityDefinition, TriggeredManaAbilityDefinition, SubAbilityDefinition)
        kind = type(value)
        if kind is CardDefinition:
            children = [self.mana(value.mana_cost)]
            for field in ('types', 'subtypes', 'colors', 'color_identity', 'keywords'):
                children.extend(node(field, value=x.name if isinstance(x, Enum) else x)
                                for x in sorted(getattr(value, field), key=str))
            for field in ('abilities', 'triggers'):
                children.append(node(field, sorted((self.encode(a) for a in getattr(value, field)), key=repr)))
            for field in ('attach_mods', 'continuous_effects', 'replacement_effects', 'enchant'):
                if getattr(value, field):
                    children.append(unknown('unsupported_' + field))
            return node('card', children, power=value.power, toughness=value.toughness,
                        enters_tapped=value.enters_tapped, legendary=value.legendary, loyalty=value.loyalty)
        ability_types = (AbilityDefinition, PriorityActionAbilityDefinition, ActivatedAbilityDefinition,
                         CastSpellAbilityDefinition, ManaAbilityDefinition, PlayLandAbilityDefinition,
                         TriggerAbilityDefinition, TriggeredManaAbilityDefinition)
        if kind in ability_types:
            children = [node('cost', (self.encode(s) for s in value.cost_subdefs)),
                        node('resolution', (self.encode(s) for s in value.action_subdefs))]
            children.extend(node('allowed_zone', value=z.name) for z in sorted(value.allowed_zones, key=str))
            for variable, spec in sorted(value.subdefs.items(), key=lambda p: p[0].name):
                children.append(node('variable', (self.encode(spec.cost_subdef), self.encode(spec.action_subdef)),
                                     role=variable.name, minimum=spec.min_value, maximum=spec.max_cap))
            if kind in (TriggerAbilityDefinition, TriggeredManaAbilityDefinition):
                children.append(self.condition(value.condition))
                if value.intervening_if is not None:
                    children.append(unknown('dynamic_intervening_condition'))
            if kind is PlayLandAbilityDefinition and value.condition is not None:
                children.append(unknown('dynamic_land_condition'))
            return node('ability', children, spell=value.is_spell, uses_stack=value.uses_stack,
                        sorcery_speed=value.sorcery_speed, mana_ability=value.is_mana_ability,
                        loyalty_cost=value.loyalty_cost,
                        triggered=kind in (TriggerAbilityDefinition, TriggeredManaAbilityDefinition))
        if kind is SubAbilityDefinition:
            effects = {e.key: e for e in value.effects}
            slots = {s.key: s for s in value.slots}
            # Keys bind authored references locally; they are never exported.
            ordered = sorted(slots, key=lambda k: (repr(self.target(slots[k])), k))
            indexes = {key: i for i, key in enumerate(ordered)}
            targets = [node('slot', (self.target(slots[key]),), index=indexes[key]) for key in ordered]
            for key in ordered:
                targets.extend(node('distinct', source=indexes[key], target=indexes[other])
                               for other in sorted(slots[key].distinct_from) if other in indexes)
            children = [self.graph(value.action_node, effects, indexes), *targets]
            if value.mana_cost is not None:
                from game.game_state.stat import Stat, ModifiableReferenceStat
                if type(value.mana_cost) in (Stat, ModifiableReferenceStat):
                    children.append(self.mana(value.mana_cost.base_value))
                else:
                    children.append(unknown('dynamic_mana_cost'))
            return node('subability', children, resolution_speed=(value.resolution_speed.name
                                                                 if value.resolution_speed else None))
        return self.effect(value)

    def graph(self, graph, effects, slots):
        from game.game_actions.data_structs.action_node import AndActionNode, OrActionNode, EffectActionNode, ManaActionNode
        if graph is None:
            return node('empty')
        kind = type(graph)
        if kind in (AndActionNode, OrActionNode):
            return node('all_of' if kind is AndActionNode else 'one_of',
                        (self.graph(child, effects, slots) for child in graph.children))
        if kind is ManaActionNode:
            children = [node('mana_requirement', (node('color', value=c.name) for c in sorted(colors, key=str)),
                             amount=amount) for colors, amount in graph.mana_req.items()]
            return node('mana', sorted(children, key=repr))
        if kind is EffectActionNode:
            children = []
            for key, target_keys in graph.effect_map.items():
                targets = [node('slot_reference', index=slots[k]) if k in slots else unknown('missing_slot')
                           for k in sorted(target_keys, key=lambda k: slots.get(k, -1))]
                children.append(node('binding', (self.encode(effects[key]) if key in effects else unknown('missing_effect'),
                                                 *targets)))
            # Mapping insertion order is the native EffectSequence order.
            return node('sequence', children)
        return unknown('unsupported_action_node')

    def mana(self, value):
        from game.mana.mana_value import (ManaValue, ImmutableManaValue, parse_mana_cost,
            GenericSymbol, ColoredSymbol, DeterministicSymbol, VariableSymbol,
            HybridGenericSymbol, PhyrexianSymbol)
        if value is None:
            return node('mana', available=False)
        if type(value) is str:
            value = parse_mana_cost(value)
        if type(value) not in (ManaValue, ImmutableManaValue):
            return unknown('unsupported_mana_cost')
        children = []
        for symbol, amount in value.items():
            kind = type(symbol)
            if kind is GenericSymbol:
                child = node('generic_mana', amount=amount)
            elif kind in (ColoredSymbol, DeterministicSymbol, PhyrexianSymbol):
                child = node('phyrexian_mana' if kind is PhyrexianSymbol else 'colored_mana',
                             (node('color', value=m.name) for m in sorted(symbol.allowed, key=str)), amount=amount)
            elif kind is HybridGenericSymbol:
                child = node('hybrid_generic_mana', amount=amount, color=symbol.mana.name)
            elif kind is VariableSymbol:
                child = node('variable_mana', amount=amount)
            else:
                child = unknown('unsupported_mana_symbol')
            children.append(child)
        return node('mana', sorted(children, key=repr), available=True)

    def target(self, slot):
        from game.target.target_selector import SingleTargetSelector, MinMaxTegetSelector
        selector = slot.target_resolver.target_selector
        if type(selector) not in (SingleTargetSelector, MinMaxTegetSelector):
            return unknown('unsupported_target_selector')
        return node('target', (self.predicate(slot.target_resolver.target_spec),),
                    minimum=selector.min_target_count, maximum=selector.max_target_count,
                    repeatable=selector.is_target_repeatable)

    def predicate(self, spec):
        from game.target.target_spec import QueryTargetSpec, PlayerQueryTargetSpec, SelfSpec
        from game.game_actions.control_effects import SpellTargetSpec
        from game.cards.starter_cards import AnyTargetSpec,PredicateTargetSpec
        if type(spec) is AnyTargetSpec:
            return node('any_damage_target', (node('types',value='CREATURE'),node('types',value='PLANESWALKER')),
                        includes_players=True)
        if type(spec) is SpellTargetSpec:
            return node('spell_target', excludes_source=True)
        if type(spec) is SelfSpec:
            return node('self_target', (unknown('dynamic_self_condition'),))
        if type(spec) is PredicateTargetSpec:
            query = unknown('dynamic_target_query') if spec.query is None or callable(spec.query) else self.query(spec.query)
            return node('card_target',(query,unknown('dynamic_target_predicate')))
        if type(spec) in (QueryTargetSpec, PlayerQueryTargetSpec):
            query = unknown('dynamic_target_query') if callable(spec.query) else self.query(spec.query)
            return node('player_target' if type(spec) is PlayerQueryTargetSpec else 'card_target', (query,))
        return unknown('unsupported_target_spec')

    def query(self, query):
        from helper.query_system.query import EqQuery, InQuery, AndQuery, OrQuery
        from game.game_state.registers import card_register as keys
        names = {getattr(keys, name): name[3:].lower() for name in
                 ('IK_ZONE', 'IK_TYPE', 'IK_SUBTYPE', 'IK_CMC', 'IK_POWER', 'IK_TOUGHNESS', 'IK_TAPPED',
                  'IK_IS_TOKEN', 'IK_HAS_DAMAGE', 'IK_HAS_TRIGGERS', 'IK_LEGENDARY')}
        if type(query) in (AndQuery, OrQuery):
            return node('and_query' if type(query) is AndQuery else 'or_query',
                        sorted((self.query(c) for c in query.children), key=repr))
        if type(query) not in (EqQuery, InQuery) or query.idx_key not in names:
            return unknown('unsupported_target_query')
        values = (query.key,) if type(query) is EqQuery else query.keys
        children = []
        for value in values:
            if isinstance(value, Enum):
                value = value.name
            if type(value) not in (str, int, float, bool):
                return unknown('bound_target_query')
            children.append(node('query_value', value=value))
        return node('equals' if type(query) is EqQuery else 'in_query', sorted(children, key=repr),
                    field=names[query.idx_key])

    def condition(self, condition):
        from game.game_actions.triggers.trigger_condition import (EventKeyCondition, ZoneChangeCondition,
            EntersBattlefieldCondition, LeavesBattlefieldCondition, DiesCondition)
        from game.game_actions.advanced_effects import SourceTappedCondition
        if type(condition) is SourceTappedCondition:
            return node('trigger', event='card_tapped', source_only=True)
        if type(condition) is EventKeyCondition:
            events = {'card_moved', 'card_tapped', 'card_untapped', 'spell_cast', 'card_drawn',
                      'life_gained', 'life_lost', 'damage_dealt', 'begin_step', 'attacker_declared'}
            return (node('trigger', event=condition.key, source_only=condition.source_only)
                    if condition.key in events else unknown('unsupported_trigger_event'))
        if type(condition) in (ZoneChangeCondition,EntersBattlefieldCondition,LeavesBattlefieldCondition,DiesCondition):
            return node('trigger', event='card_moved', origin=condition.origin.name if condition.origin else None,
                        destination=condition.destination.name if condition.destination else None,
                        source_only=condition.source_only, another=condition.another,
                        controlled_only=condition.controlled_only,
                        card_type=condition.card_type.name if condition.card_type else None)
        return unknown('unsupported_trigger_condition')

    def effect(self, effect):
        from game.game_actions.card_effects import DamagePlayerEffect, MoveSourceEffect, TapSourceEffect
        from game.game_actions.advanced_effects import DrawEffect, DamageCreaturesEffect, TemporaryBuffEffect, SacrificeEffect
        from game.game_actions.control_effects import CounterSpellEffect, DestroyAllCreaturesEffect
        from game.game_actions.mana_effects import AddManaEffect
        from game.game_actions.permanent_effects import AttachSourceEffect, CreateTokenEffect
        from game.game_actions.query_effects import MoveMatchingCardsEffect
        from game.cards.starter_cards import (DamageTargetEffect, DestroyEffect, DrawCardsEffect, GainLifeEffect,
            CreateTokensEffect, CreateAttackingTokensEffect, TemporaryModifierEffect, PutCounterEffect)
        kind = type(effect)
        if kind in (DamagePlayerEffect, DamageTargetEffect, DamageCreaturesEffect):
            return node('damage', amount=effect.amount, recipient=('player' if kind is DamagePlayerEffect else
                                                                  'creature' if kind is DamageCreaturesEffect else 'target'))
        if kind in (DrawEffect, DrawCardsEffect):
            return node('draw', amount=effect.count if kind is DrawCardsEffect else 1, recipient='controller')
        if kind is GainLifeEffect:
            return node('gain_life', amount=effect.amount, recipient='controller')
        if kind is MoveSourceEffect:
            return node('move_zone', destination=effect.destination.name, recipient='source')
        if kind is TapSourceEffect:
            return node('tap', recipient='source')
        if kind is DestroyEffect:
            return node('destroy', recipient='target')
        if kind is CounterSpellEffect:
            return node('counter', recipient='spell')
        if kind is DestroyAllCreaturesEffect:
            return node('destroy', scope='all_creatures', simultaneous=True)
        if kind is SacrificeEffect:
            return node('sacrifice', recipient='target', destination='GRAVEYARD')
        if kind is TemporaryBuffEffect:
            return node('modify_stat', power=2, toughness=2, duration='cleanup', recipient='target')
        if kind is AddManaEffect:
            return node('add_mana', amount=effect.amount, color=effect.mana.name, recipient='controller')
        if kind is AttachSourceEffect:
            return node('attach', recipient='target',entering=effect.entering)
        if kind is MoveMatchingCardsEffect:
            return node('move_zone',(self.predicate(effect.spec),),destination=effect.destination.name,scope='query_at_resolution')
        if kind is PutCounterEffect:
            children = [node('counter_type',value=effect.counter.name)]
            if effect.target_getter is not None:
                children.append(unknown('dynamic_counter_target'))
            return node('put_counter',children,amount=effect.amount,recipient='source' if effect.source else 'unspecified')
        if kind is TemporaryModifierEffect:
            from game.game_state.modifier import AddIntModifier, AddSetModifier, SetModifier
            from game.stat_type import STAT_POWER,STAT_TOUGHNESS,STAT_KEYWORDS
            fields = {STAT_POWER:'power',STAT_TOUGHNESS:'toughness',STAT_KEYWORDS:'keywords'}
            children = []
            for stat,modifiers in effect.modifiers.items():
                for modifier in modifiers:
                    if stat not in fields or type(modifier) not in (AddIntModifier,AddSetModifier,SetModifier):
                        children.append(unknown('unsupported_modifier'))
                    elif type(modifier.value) in (int,float,bool):
                        children.append(node('modifier',field=fields[stat],amount=modifier.value,
                                             operation='set' if type(modifier) is SetModifier else 'add'))
                    elif type(modifier.value) is frozenset and all(type(v) is str for v in modifier.value):
                        children.append(node('modifier',(node('keywords',value=v) for v in sorted(modifier.value)),
                                             field=fields[stat],operation='set' if type(modifier) is SetModifier else 'add'))
                    else:
                        children.append(unknown('unsupported_modifier_value'))
            if effect.target_getter is not None:
                children.append(unknown('dynamic_modifier_target'))
            return node('modify_stat',children,duration='cleanup',recipient='target',fallback_source=True)
        if kind in (CreateTokenEffect, CreateTokensEffect, CreateAttackingTokensEffect):
            children = [self.encode(effect.definition)]
            if kind is not CreateTokenEffect and effect.count_getter is not None:
                children.append(unknown('dynamic_token_count'))
            return node('create_token', children, amount=effect.count,
                        attacking=kind is CreateAttackingTokensEffect)
        return unknown('unsupported_effect')


class DeckSemanticEncoder(CardSemanticEncoder):
    """Reviewed additions for deck schema 3; preserve schema-2 input behavior."""

    def condition(self, condition):
        from game.cards.starter_cards import LifeGainCondition
        from game.game_actions.triggers.trigger_condition import EventKeyCondition
        if type(condition) is LifeGainCondition:
            return node('trigger', event='life_gained', controlled_only=True)
        if type(condition) is EventKeyCondition and condition.key == 'counter_placed':
            return node('trigger', event='counter_placed', source_only=condition.source_only)
        return super().condition(condition)

    def effect(self, effect):
        from game.cards.starter_support import ScryEffect
        if type(effect) is ScryEffect:
            return node('selection', amount=effect.amount, selection_kind='scry', recipient='controller')
        return super().effect(effect)


class TacticalSemanticEncoder(DeckSemanticEncoder):
    """Schema-4 reviewed native conditions; arbitrary callbacks remain unknown."""
    def condition(self, condition):
        from game.cards.starter_cards import (AttackCondition, OneOrMoreAttackCondition,
            EnteredSmallCreatureCondition, BlockedCondition)
        from game.game_actions.triggers.trigger_condition import StepCondition, SpellCastCondition, DamageDealtCondition
        if type(condition) is AttackCondition:
            return node('trigger', event='attacker_declared', source_only=condition.source_only,
                        controlled_only=condition.controlled_only, power_at_most=condition.power_at_most)
        if type(condition) is OneOrMoreAttackCondition:
            return node('trigger', event='attackers_declared', controlled_only=True, minimum=1)
        if type(condition) is EnteredSmallCreatureCondition:
            return node('trigger', event='card_moved', destination='BATTLEFIELD', controlled_only=True,
                        another=True, card_type='CREATURE', power_at_most=2)
        if type(condition) is BlockedCondition:
            return node('trigger', event='attacker_blocked', source_only=True)
        if type(condition) is StepCondition:
            return node('trigger', event='phase_started', phase=getattr(condition.phase,'name',condition.phase),
                        controlled_only=condition.controller_turn_only)
        if type(condition) is SpellCastCondition:
            return node('trigger', event='spell_cast', controlled_only=condition.controlled_only, source_only=condition.source_only)
        if type(condition) is DamageDealtCondition:
            return node('trigger', event='damage_dealt', source_only=condition.source_only,
                        combat_only=condition.combat_only, to_player_only=condition.to_player_only, minimum=1)
        return super().condition(condition)
