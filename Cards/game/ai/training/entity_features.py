"""Schema 2 ragged actor entities, semantic trees and complete action arguments.

Identity strings only resolve local references. They never become numerical
features. Entity order is arbitrary except explicit stack/selection positions.
Unsupported semantics are represented by unknown markers, never inferred from
card names, authored keys, callbacks or actual sampled hidden cards.
"""
from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar
from enum import Enum
from math import copysign, log1p, isfinite
import json

from game.enums import CardType, CardSubtype, ManaType, ZoneType, CounterType, ResolutionSpeed, SAVariableType
from game.ai.evaluation.search_encoding import EncodingContext, CARD_FEATURES, PLAYER_FEATURES, GLOBAL_FEATURES, ACTION_FEATURES
from game.ai.evaluation.semantic_encoding import SEMANTIC_VERSION, CardSemanticEncoder
from game.ai.evaluation.evaluation_cache import EvaluationCache

FEATURE_VERSION = ENCODER_VERSION = 2
ZONES = ('hand', 'battlefield', 'stack', 'graveyard', 'exile')
SUBTYPES = tuple(x.name for x in CardSubtype)
COUNTERS = tuple(x.name for x in CounterType)
KINDS = ('unknown', 'card', 'ability', 'cost', 'resolution', 'subability', 'all_of', 'one_of', 'sequence',
         'binding', 'empty', 'slot', 'slot_reference', 'distinct', 'target', 'card_target', 'player_target',
         'self_target', 'spell_target', 'and_query', 'or_query', 'equals', 'in_query', 'query_value',
         'trigger', 'variable', 'allowed_zone', 'types', 'subtypes', 'colors', 'color_identity', 'keywords',
         'abilities', 'triggers', 'mana', 'mana_requirement', 'color', 'generic_mana', 'colored_mana',
         'phyrexian_mana', 'hybrid_generic_mana', 'variable_mana', 'damage', 'draw', 'gain_life', 'move_zone',
         'tap', 'destroy', 'counter', 'sacrifice', 'modify_stat', 'add_mana', 'attach', 'create_token',
         'modifier','counter_type','put_counter','any_damage_target')
NUMBERS = ('amount', 'power', 'toughness', 'loyalty', 'minimum', 'maximum', 'index', 'source', 'target',
           'loyalty_cost', 'available', 'enters_tapped', 'legendary', 'spell', 'uses_stack', 'sorcery_speed',
           'mana_ability', 'triggered', 'repeatable', 'excludes_source', 'source_only', 'another',
           'controlled_only', 'simultaneous', 'attacking','entering','includes_players','fallback_source')
ENUM_VALUES = tuple(dict.fromkeys(x.name for enum in (CardType, CardSubtype, ManaType, ZoneType,CounterType) for x in enum))
CATEGORIES = {
    'value': (*ENUM_VALUES, 'flying', 'reach', 'first strike', 'double strike', 'deathtouch', 'lifelink',
              'trample', 'vigilance', 'haste', 'defender', 'indestructible', 'hexproof', "can't attack", "can't block"),
    'destination': tuple(x.name for x in ZoneType), 'origin': tuple(x.name for x in ZoneType),
    'recipient': ('player', 'creature', 'target', 'controller', 'source', 'spell','unspecified'),
    'operation': ('set','add'),
    'color': tuple(x.name for x in ManaType), 'duration': ('cleanup',), 'scope': ('all_creatures','query_at_resolution'),
    'field': ('zone', 'type', 'subtype', 'cmc', 'power', 'toughness', 'keywords','tapped', 'is_token', 'has_damage', 'has_triggers', 'legendary'),
    'event': ('card_moved', 'card_tapped', 'card_untapped', 'spell_cast', 'card_drawn', 'life_gained', 'life_lost',
              'damage_dealt', 'begin_step', 'attacker_declared'),
    'role': tuple(x.name for x in SAVariableType), 'resolution_speed': tuple(x.name for x in ResolutionSpeed),
    'card_type': tuple(x.name for x in CardType),
}
REQUESTS = ('PriorityDecisionRequest', 'AbilityDecisionRequest', 'DeclareAttackersRequest', 'DeclareBlockersRequest',
            'MulliganRequest', 'MulliganBottomRequest', 'DiscardRequest', 'ScryRequest', 'CombatDamageRequest',
            'TriggerOrderRequest', 'ReplacementOrderRequest', 'OptionalEffectRequest', 'AbilityResolutionRequest',
            'StartingPlayerRequest', 'LegendRequest', 'ReplacementRequest', 'ReplacementAcceptanceRequest')
GROUPS = 12  # self/opponent x five card zones, then self/opponent player
SEMANTIC_SIZE = 2*len(KINDS) + 3 + 2*len(NUMBERS) + sum(len(v)+1 for v in CATEGORIES.values()) + 1
ENTITY_SIZE = 2 + len(CARD_FEATURES) + len(PLAYER_FEATURES) + len(SUBTYPES) + len(COUNTERS) + 3
GLOBAL_SIZE = len(GLOBAL_FEATURES) + len(REQUESTS) + 1
RELATION_SIZE = 5 + 3  # attachment/block/attack/stack-source/stack-target, position, count, known
ARGUMENT_SIZE = 4 + 3 + 4  # target/mana/parameter/selection, role, group/order/count/value
ACTION_SIZE = len(ACTION_FEATURES)


class SemanticRows(tuple):
    """Validated deeply immutable numeric facts, independent of model weights.

    Public construction validates every number. Only the local encoder's
    already-normalized rows and compositions of this exact type use the fast
    constructor. JSON input still enters through ordinary tuples and validation.
    """
    __slots__ = ()

    def __new__(cls, rows=()):
        rows = tuple(rows)
        if any(type(row) is not tuple or len(row)!=SEMANTIC_SIZE or
               any(type(v) not in (int,float) or not isfinite(v) for v in row) for row in rows):
            raise ValueError('Semantic rows must be immutable finite numeric rows')
        return tuple.__new__(cls,rows)

    def __add__(self, other):
        if type(other) is SemanticRows or type(other) is tuple and not other:
            if not other:
                return self
            if not self:
                return other
            return tuple.__new__(SemanticRows,tuple.__add__(self,other))
        return tuple.__add__(self,other)


def _join_semantics(parts):
    parts = tuple(p for p in parts if p)
    if len(parts)==1:
        return parts[0]
    rows = tuple(row for part in parts for row in part)
    return tuple.__new__(SemanticRows,rows) if all(type(p) is SemanticRows for p in parts) else rows


def normalized(values):
    if any(type(v) not in (int, float, bool) or not isfinite(v) for v in values):
        raise ValueError('Entity features must contain finite numbers')
    return tuple(0. if v == 0 else copysign(log1p(abs(v)), v) for v in values)


def hot(value, vocabulary):
    return tuple(float(value == x) for x in vocabulary)


def semantic_rows(description):
    """Preserve kind, parent, phase and sibling position before learned pooling."""
    result = []
    def visit(data, parent='empty', depth=0, position=0, phase=0):
        if not isinstance(data, dict) or data.get('schema_version') != SEMANTIC_VERSION or depth > 64:
            data = dict(kind='unknown', attributes=(), children=(), schema_version=SEMANTIC_VERSION)
        kind = data.get('kind', 'unknown')
        if kind not in KINDS:
            kind = 'unknown'
        attrs = dict(data.get('attributes', ()))
        phase = 1 if kind == 'cost' else 2 if kind == 'resolution' else phase
        values = [*hot(kind, KINDS), *hot(parent, KINDS), depth, position, phase]
        for key in NUMBERS:
            value = attrs.get(key)
            values.extend((float(value) if type(value) in (int, float, bool) else 0., value is not None))
        for key, vocabulary in CATEGORIES.items():
            value = attrs.get(key)
            values.extend((*hot(value, vocabulary), value is not None and value not in vocabulary))
        values.append(kind == 'unknown')
        row = normalized(values)
        assert len(row) == SEMANTIC_SIZE
        result.append(row)
        for index, child in enumerate(data.get('children', ())):
            visit(child, kind, depth+1, index, phase)
    visit(description)
    return tuple.__new__(SemanticRows,result)  # normalized() and width assertion above prove each row.


def description_semantics(value):
    """Read projected facts only; missing schema-2 semantics stay explicit."""
    if not isinstance(value, dict):
        return semantic_rows(None)
    return semantic_rows(value.get('semantic'))


def collect_semantics(value, encode=semantic_rows):
    if isinstance(value, dict):
        if 'semantic' in value:
            return encode(value['semantic'])
        return _join_semantics(collect_semantics(child,encode) for child in value.values())
    if isinstance(value, (list, tuple)):
        return _join_semantics(collect_semantics(child,encode) for child in value)
    return SemanticRows()


def _rows(rows, width):
    if type(rows) is SemanticRows and width == SEMANTIC_SIZE:
        return True
    return type(rows) is tuple and all(type(r) is tuple and len(r) == width
        and all(type(v) in (int, float) and isfinite(v) for v in r) for r in rows)


@dataclass(frozen=True)
class EntityState:
    schema_version: ClassVar[int] = 2
    global_width: ClassVar[int] = GLOBAL_SIZE
    entity_width: ClassVar[int] = ENTITY_SIZE
    relation_width: ClassVar[int] = RELATION_SIZE
    globals: tuple
    entities: tuple
    semantics: tuple
    groups: tuple
    relations: tuple
    feature_version: int = FEATURE_VERSION

    def validate(self):
        # Inputs are deeply immutable; repeated optimizer steps need not rescan
        # the same thousands of numeric facts in Python on every forward pass.
        self._validated_record

    @cached_property
    def _validated_record(self):
        if (self.feature_version != self.schema_version or len(self.globals) != self.global_width
                or not _rows((self.globals,), self.global_width) or not _rows(self.entities, self.entity_width)
                or type(self.semantics) is not tuple or type(self.groups) is not tuple
                or type(self.relations) is not tuple
                or any(type(r) is not tuple or len(r)!=3 for r in self.relations)
                or len(self.semantics) != len(self.entities) or len(self.groups) != len(self.entities)
                or any(not _rows(rows, SEMANTIC_SIZE) for rows in self.semantics)
                or any(type(g) is not int or not 0 <= g < GROUPS for g in self.groups)):
            raise ValueError('Invalid entity state schema')
        for source, target, features in self.relations:
            if not (type(source) is int and type(target) is int
                    and 0 <= source < len(self.entities) and 0 <= target < len(self.entities)
                    and _rows((features,), self.relation_width)):
                raise ValueError('Invalid entity relation')
        return True


@dataclass(frozen=True, slots=True)
class EntityArgument:
    features: tuple
    source: int
    target: int
    semantics: tuple = ()


@dataclass(frozen=True)
class EntityAction:
    action_width: ClassVar[int] = ACTION_SIZE
    argument_width: ClassVar[int] = ARGUMENT_SIZE
    features: tuple
    source: int
    semantics: tuple
    arguments: tuple

    @cached_property
    def _validated_record(self):
        if (not _rows((self.features,), self.action_width) or not _rows(self.semantics, SEMANTIC_SIZE)
                or type(self.arguments) is not tuple or any(type(a) is not EntityArgument for a in self.arguments)
                or any(not _rows((a.features,), self.argument_width) or not _rows(a.semantics, SEMANTIC_SIZE)
                       for a in self.arguments)):
            raise ValueError('Invalid entity action schema')
        return True

    def validate(self, state):
        self._validated_record
        references = (self.source, *(r for a in self.arguments for r in (a.source, a.target)))
        if any(type(r) is not int or not -1 <= r < len(state.entities) for r in references):
            raise ValueError('Invalid entity action schema')


def features_from_dict(state, actions):
    def rows(value): return tuple(tuple(r) for r in value)
    state_class = EntityState
    if state.get('feature_version') == 3:
        from game.ai.training.deck_features import DeckEntityState
        state_class = DeckEntityState
    elif state.get('feature_version') == 4:
        from game.ai.training.tactical_features import TacticalEntityState
        state_class = TacticalEntityState
    action_class = EntityAction
    if state.get('feature_version') == 4:
        from game.ai.training.tactical_features import TacticalEntityAction
        action_class = TacticalEntityAction
    state = state_class(tuple(state['globals']), rows(state['entities']),
                        tuple(rows(r) for r in state['semantics']), tuple(state['groups']),
                        tuple((s,t,tuple(f)) for s,t,f in state['relations']), state['feature_version'])
    result = tuple(action_class(tuple(a['features']), a['source'], rows(a['semantics']),
                   tuple(EntityArgument(tuple(arg['features']), arg['source'], arg['target'], rows(arg['semantics']))
                         for arg in a['arguments']),
                   **({'argument_parents':tuple(a['argument_parents'])} if state.feature_version == 4 else {})) for a in actions)
    return state, result


class EntityFeatureEncoder:
    """Read detached actor information; local indices carry no learned identity."""
    def __init__(self):
        # Static numeric facts may be shared across revisions; learned embeddings
        # are recomputed in the current forward graph, including during training.
        self.semantic_cache = EvaluationCache(256,16*1024*1024)

    def _semantic(self, description):
        key = ('semantic',json.dumps(description,sort_keys=True,separators=(',',':'),allow_nan=False))
        found = self.semantic_cache.get(key)
        if found is not None:
            return found
        rows = semantic_rows(description)
        return self.semantic_cache.put(key,rows,len(rows)*SEMANTIC_SIZE)

    def encode(self, information):
        context = EncodingContext(information)
        native = context.state
        view = information.current.observation()
        cards = [(c,z) for z in ZONES for c in view.get(z, ())]
        entities, semantics, groups = [], [], []
        for row, (card, zone) in zip(native.cards, cards):
            visible = card.get('identity_visible', True)
            extras = [*(float(s in card.get('subtypes', ())) if visible else 0. for s in SUBTYPES),
                      *(float(card.get('counters', {}).get(c, 0)) for c in COUNTERS), 0., 0., 0.]
            entities.append((*normalized((1.,0.,*row,*(0.,)*len(PLAYER_FEATURES))), *normalized(extras)))
            rows = (collect_semantics(card.get('abilities', ()),self._semantic)
                    +collect_semantics(card.get('triggers', ()),self._semantic)) if visible else ()
            if visible and (card.get('abilities') or card.get('triggers')) and not rows:
                rows = self._semantic(None)
            semantics.append(rows)
            own = card.get('controller') == information.current.player
            groups.append((0 if own else 5) + ZONES.index(zone))
        for index, row in enumerate(native.players):
            entities.append(normalized((0.,1.,*(0.,)*len(CARD_FEATURES),*row,
                                        *(0.,)*(len(SUBTYPES)+len(COUNTERS)+3))))
            semantics.append(())
            groups.append(10+index)
        relations = [(s,t,normalized((*hot(kind, range(5)),0.,1.,1.))) for kind,s,t in native.edges]
        for item in view.get('pending_abilities', ()):
            if not isinstance(item, dict):
                continue
            index = len(entities)
            features = [0.]*ENTITY_SIZE
            features[-3:] = [1., item.get('position_from_bottom', 0), item.get('resolves_next', False)]
            entities.append(normalized(features))
            semantics.append(collect_semantics(item.get('effects'),self._semantic) or self._semantic(None))
            groups.append(2 if item.get('controller') == information.current.player else 7)
            source = context.reference(item.get('source'))
            if source >= 0:
                relations.append((index,source,normalized((*hot(3,range(5)),item.get('position_from_bottom',0),1.,1.))))
            def references(data):
                if isinstance(data, dict):
                    ref = context.reference(data)
                    if ref >= 0:
                        yield ref
                    else:
                        for child in data.values(): yield from references(child)
                elif isinstance(data, (list, tuple)):
                    for child in data: yield from references(child)
            for order, target in enumerate(references(item.get('targets'))):
                relations.append((index,target,normalized((*hot(4,range(5)),order,1.,1.))))
        state = EntityState(normalized((*native.globals,*hot(information.current.request_kind,REQUESTS),
                                        information.current.request_kind not in REQUESTS)),
                            tuple(entities),tuple(semantics),tuple(groups),tuple(relations))
        actions = tuple(self.action(a,context) for a in information.actions)
        state.validate()
        for action in actions: action.validate(state)
        return state, actions

    def action(self, action, context):
        encoded = context.action(action)
        data = action.describe()
        effects = []
        all_semantics = self._semantic(data['ability_semantic']) if 'ability_semantic' in data else SemanticRows()
        for role, part in enumerate((data.get('cost') or {}, data.get('effect') or {})):
            for index, effect in enumerate(part.get('effects', ())):
                semantic = effect.get('semantic')
                if semantic is not None:
                    semantic = dict(kind='cost' if role == 0 else 'resolution', attributes=(('index',index),),
                                    children=(semantic,),schema_version=SEMANTIC_VERSION)
                rows = self._semantic(semantic)
                all_semantics += rows
                effects.append((role,index,rows))
        arguments = []
        def add(kind, role, group, order, count=1., value=0., source=-1, target=-1, rows=()):
            arguments.append(EntityArgument(normalized((*hot(kind,range(4)),*hot(role,range(3)),group,order,count,value)),
                                            source,target,rows))
        for role, group, source, target, count in encoded.bindings:
            matched = [(r,i,rows) for (r,i,rows), slots in zip(effects,encoded.effect_slots) if r==role and group in slots]
            if matched:
                for _, index, rows in matched:
                    add(0,role,group,index,count,source=source,target=target,rows=rows)
            else:
                add(0,role,group,0,count,source=source,target=target)
        for index, source in enumerate(encoded.mana_sources): add(1,0,0,index,target=source)
        for role, group, value in encoded.parameters: add(2,role,group,0,value=value)
        for group, selection in enumerate(encoded.selections):
            for order, target in enumerate(selection): add(3,2,group,order,target=target)
        return EntityAction(normalized(encoded.features),encoded.source,all_semantics,tuple(arguments))
