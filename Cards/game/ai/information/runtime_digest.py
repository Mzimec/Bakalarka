"""World/viewer-owned incremental identities, with the existing typed hash format.

Nodes retain scalar tokens and child digests, never historical runtime objects.
Portable seeds may reuse an already available immutable root input; they do not
build another observation export. Native shortcuts validate inputs; other cards
use live fields with incremental branch hashing. Trusted restores may share
bounded portable seeds, never reader handles or runtime references.
"""
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from hashlib import blake2b
import json

from game.ai.information.structured_values import (
    DigestReference, ReadOnlyObject, StructuredJSON, Value, structural_digest,
)


class DigestReader:
    """Internal synchronous reader protocol; ordinary hashing stays independent."""
    __slots__ = ()


PRIMITIVE_FIELDS = ('id', 'zone_revision', 'owner', 'controller', 'zone', 'face_down',
    'tapped', 'damage_marked', 'damage_by_deathtouch', 'name', 'token', 'legendary',
    'text', 'chosen_x', 'summoning_sick', 'loyalty_activated_this_turn')
PRIMITIVE_TYPES = ((str,), (int,), (int,), (int,), (str,), (bool,), (bool, type(None)),
    (int,), (bool,), (str,), (bool,), (bool,), (str,), (int,), (bool,), (bool,))
BASE_FIELDS = ('types', 'subtypes', 'colors', 'keywords', 'power', 'toughness')
VARIABLE_FIELDS = ('attached_to', 'skip_untap', 'abilities', 'triggers',
    'static_abilities', 'attachment_restrictions', 'cost', 'casting_cost', 'mana_value')
FIXED_PARTS = {'counters': structural_digest({}), 'attachments': structural_digest(()),
               'identity_visible': structural_digest(True)}
CARD_FIELDS = tuple(sorted((*PRIMITIVE_FIELDS, *BASE_FIELDS, *VARIABLE_FIELDS, *FIXED_PARTS)))
CARD_ZONES = frozenset(('hand', 'battlefield', 'graveyard', 'exile', 'stack'))
_BASE_PARTS = OrderedDict()


def base_token(values):
    """Content-only native six-field key. No enums, callbacks or runtime objects."""
    types, subtypes, colors, keywords, power, toughness = values
    if (any(type(items) is not tuple or any(type(v) is not str for v in items)
            for items in (types, subtypes, colors, keywords))
            or type(power) not in (int, type(None)) or type(toughness) not in (int, type(None))):
        return None
    return values


def base_parts(token, parts=None):
    """Bounded shared cache of pure characteristic content, independent of viewer."""
    entry = _BASE_PARTS.get(token)
    if entry is not None:
        _BASE_PARTS.move_to_end(token)
        return entry
    entry = tuple(structural_digest(v) for v in token) if parts is None else parts
    _BASE_PARTS[token] = entry
    if len(_BASE_PARTS) > 512:
        _BASE_PARTS.popitem(last=False)
    return entry


def card_base_parts(row, inputs):
    base = inputs[1]
    if row._digest_base is not base:
        token = base_token((base['types'], base['subtypes'], tuple(row['colors']),
                            base['keywords'], base['power'], base['toughness']))
        row._digest_base_parts = (base_parts(token) if token is not None else
            tuple(structural_digest(row[key]) for key in BASE_FIELDS))
        row._digest_base = base
    return row._digest_base_parts


@dataclass(frozen=True, slots=True)
class NativeSeed:
    primitives: tuple
    base: tuple
    variables: tuple
    digest: bytes

    def matches(self, inputs, base):
        return self.primitives == inputs[2] and self.base == base and self.variables == inputs[3]


class RootDigestSeed:
    """Already available immutable root input; read once, with no native getters."""
    def __init__(self, player, value):
        self.player, self.value = player, value
        self.cards, self.native = {}, {}
        for key, zone in value.items:
            if key not in CARD_ZONES or zone.kind != 'array':
                continue
            for row in zone.items:
                if row.kind != 'object':
                    continue
                fields = dict(row.items)
                identifier = fields.get('id')
                if identifier is None or type(identifier.items) is not str:
                    continue
                self.cards[identifier.items] = row
                if tuple(fields) != CARD_FIELDS or any(fields[k].digest != v for k, v in FIXED_PARTS.items()):
                    continue
                primitives = tuple(fields[k].items for k in PRIMITIVE_FIELDS)
                if any(type(v) not in allowed for v, allowed in zip(primitives, PRIMITIVE_TYPES)):
                    continue
                parts = tuple(fields[k].digest for k in BASE_FIELDS)
                self.native[identifier.items] = NativeSeed(primitives, parts,
                    tuple(fields[k].digest for k in VARIABLE_FIELDS), row.digest)
                arrays = (tuple(tuple(child.items for child in fields[k].items) for k in BASE_FIELDS[:4])
                          if all(fields[k].kind == 'array' for k in BASE_FIELDS[:4]) else None)
                token = base_token((*arrays, fields['power'].items, fields['toughness'].items)) if arrays is not None else None
                if token is not None:
                    base_parts(token, parts)

    @classmethod
    def information(cls, info):
        from game.ai.information.search_observation import ActingPlayerObservation
        if info is None or type(info.current) is not ActingPlayerObservation:
            return None
        value = info.current.structured.value
        if value.kind != 'object':
            return None
        fields = dict(value.items)
        if 'schema_version' not in fields or 'self' not in fields or fields['self'].items != info.current.player:
            return None
        return cls(info.current.player, value)


class DigestSeedBank:
    """One sampled world's bounded data-only seeds. Explicit trusted copies share it."""
    def __init__(self, root=None, limit=512):
        self.root, self.limit = root, limit
        self.first, self.recent, self.views = {}, OrderedDict(), {}

    def __reduce__(self):
        # Generic copying/filtering must not inherit another world's private input.
        return type(self), ()

    def __deepcopy__(self, memo):
        return type(self)()

    def native(self, player, identifier, inputs, base):
        if self.root is not None and self.root.player == player:
            record = self.root.native.get(identifier)
            if record is not None and record.matches(inputs, base):
                return record, 'input'
        key = player, identifier, 'native'
        for source, record in (('first', self.first.get(key)), ('recent', self.recent.get(key))):
            if record is not None and record.matches(inputs, base):
                return record, source
        return None, None

    def generic(self, player, identifier):
        if self.root is not None and self.root.player == player and identifier in self.root.cards:
            return self.root.cards[identifier]
        key = player, identifier, 'generic'
        return self.recent.get(key, self.first.get(key))

    def has_native(self, player, identifier):
        key = player, identifier, 'native'
        return ((self.root is not None and self.root.player == player and identifier in self.root.native)
                or key in self.first or key in self.recent)

    def publish(self, player, identifier, kind, value):
        key = player, identifier, kind
        if key not in self.first and len(self.first) < self.limit:
            self.first[key] = value
        else:
            self.recent[key] = value
            self.recent.move_to_end(key)
            if len(self.recent) > self.limit:
                self.recent.popitem(last=False)

    def view(self, player):
        if self.root is not None and self.root.player == player:
            return self.root.value
        return self.views.get(player)

    def publish_view(self, player, node):
        if (player not in self.views and len(self.views) < self.limit
                and (self.root is None or self.root.player != player)):
            self.views[player] = node.freeze()


@lru_cache(maxsize=256)
def object_key(name):
    encoded = name.encode('utf-8', 'surrogatepass')
    return len(encoded).to_bytes(8, 'little'), encoded


def object_digest(names, parts):
    result = blake2b(digest_size=20, person=b'card-search-v1')
    # The byte stream is identical to the scalar update protocol. join() and
    # hashlib consume one batch in native code instead of three updates/field.
    result.update(b'object' + b''.join(prefix + digest
        for prefix, digest in zip(object_prefixes(tuple(names)), parts)))
    return result.digest()


def object_prefixes(names):
    # Keep enormous extension schemas out of the process-wide metadata cache.
    if len(names) > 128:
        return tuple(size + encoded for size, encoded in map(object_key, names))
    return _small_object_prefixes(names)


@lru_cache(maxsize=256)
def _small_object_prefixes(names):
    return tuple(size + encoded for size, encoded in map(object_key, names))


class DigestNode:
    """One world/viewer's branch of the incremental identity tree.

    Mapping/sequence reads replace only changed child digests; matching typed
    children reuse the previous digest. The node belongs to ObservationReaders,
    not to an MCTS tree. freeze() exports detached seed data for trusted restore;
    it is not a game-state snapshot and cannot restore rules execution.
    """
    __slots__ = ('shape', 'children', 'parts', 'digest', 'token', 'counts', '_frozen', '_pending', '_mapping_keys')

    def __init__(self, counts):
        self.shape = self.parts = self.digest = self.token = None
        self.children = ()
        self.counts = counts
        self._frozen = None
        self._pending = None
        self._mapping_keys = None

    def freeze(self):
        if self._frozen is None:
            self._expand()
            self._frozen = (self.shape, self.parts, self.digest, self.token,
                            tuple(child.freeze() for child in self.children))
        return self._frozen

    def _expand(self):
        if self._pending is None:
            return
        seed, root_view = self._pending
        if type(seed) is Value:
            if seed.kind == 'object':
                self.children = tuple(type(self).seeded(v, self.counts)
                    if not (root_view and k in CARD_ZONES) else type(self)._card_array(v, self.counts)
                    for k, v in seed.items)
            else:
                self.children = tuple(type(self).seeded(v, self.counts) for v in seed.items)
        else:
            self.children = tuple(type(self).seeded(v, self.counts) for v in seed)
        self._pending = None

    @classmethod
    def seeded(cls, seed, counts, *, root_view=False):
        node = cls(counts)
        if seed is None:
            return node
        if type(seed) is Value:
            node.digest = seed.digest
            if not root_view:
                node._frozen = seed
            if seed.kind == 'object':
                names = tuple(key for key, _ in seed.items)
                node.shape, node.parts = ('object', names), tuple(v.digest for _, v in seed.items)
                node._pending = seed, root_view
            elif seed.kind == 'array':
                node.shape, node.parts = ('array', len(seed.items)), tuple(v.digest for v in seed.items)
                node._pending = seed, False
            else:
                value = seed.items
                node.shape, node.token = 'scalar', (type(value), repr(value) if type(value) is float else value)
            return node
        node.shape, node.parts, node.digest, node.token, children = seed
        node._pending = children, False
        node._frozen = seed
        return node

    @classmethod
    def _card_array(cls, value, counts):
        if value.kind != 'array':
            return cls.seeded(value, counts)
        node = cls(counts)
        node.shape, node.parts, node.digest = ('array', len(value.items)), tuple(v.digest for v in value.items), value.digest
        node.children = tuple(cls(counts) for _ in value.items)
        return node

    def update(self, value):
        if isinstance(value, DigestReader):
            return value.identity_digest()
        if type(value) is ReadOnlyObject or isinstance(value, (DigestReference, StructuredJSON, Value)):
            return structural_digest(value)
        kind = type(value)
        if value is None or kind in (str, int, float, bool):
            # Python equality merges False/0 and +/-0.0; typed identity does not.
            token = kind, repr(value) if kind is float else value
            if self.shape != 'scalar' or self.token != token:
                self.digest = structural_digest(value)
                self.shape, self.token, self.children, self.parts = 'scalar', token, (), None
                self._frozen = None
                self._pending = None
            return self.digest
        if isinstance(value, Mapping):
            return self.mapping(value)
        if kind in (list, tuple):
            self._expand()
            shape = 'array', len(value)
            if self.shape != shape:
                old = self.children if isinstance(self.shape, tuple) and self.shape[0] == 'array' else ()
                self.children = old[:len(value)] + tuple(
                    DigestNode(self.counts) for _ in range(max(0, len(value) - len(old))))
                self.shape, self.parts, self.token = shape, None, None
                self._frozen = None
            parts = tuple(child.update(item) for child, item in zip(self.children, value))
            if self.parts != parts:
                result = blake2b(digest_size=20, person=b'card-search-v1')
                result.update(b'array')
                for digest in parts:
                    result.update(digest)
                self.digest, self.parts = result.digest(), parts
                self._frozen = None
                self.counts['digest_branches_rehashed'] += 1
            return self.digest
        # Keep the same error and admissible input contract as full hashing.
        return structural_digest(value)

    def mapping(self, value):
        """Authoritative incremental path; schema caching is an explicit trial."""
        keys = {}
        for key in value:
            if not isinstance(key, (str, int, float, bool)) and key is not None:
                raise TypeError('Observation keys must be JSON scalars')
            if type(key) not in (str, int, float, bool, type(None)):
                return structural_digest(value)
            name = key if isinstance(key, str) else json.dumps(key, allow_nan=False)
            keys[name] = key
        names = tuple(sorted(keys))
        shape = 'object', names
        self._expand()
        if self.shape != shape:
            old = dict(zip(self.shape[1], self.children)) if (
                isinstance(self.shape, tuple) and self.shape[0] == 'object') else {}
            self.children = tuple(old[name] if name in old else DigestNode(self.counts) for name in names)
            self.shape, self.parts, self.token = shape, None, None
            self._frozen = None
        parts = tuple(child.update(value[keys[name]]) for name, child in zip(names, self.children))
        if self.parts != parts:
            self.digest, self.parts = object_digest(names, parts), parts
            self._frozen = None
            self.counts['digest_branches_rehashed'] += 1
        return self.digest

    def mapping_cached(self, value):
        """Experimental schema reuse; identical typed identities and live reads."""
        # An ordered string-key schema is reusable; values and every child are
        # still read live. Exact types prevent bool/int or custom-key equality
        # from making a changed JSON schema look unchanged.
        incoming = tuple(value)
        strings = all(type(key) is str for key in incoming)
        if (strings and incoming == self._mapping_keys and isinstance(self.shape, tuple)
                and self.shape[0] == 'object'):
            names = self.shape[1]
            keys = None
            self.counts['digest_mapping_schema_hits'] += 1
        else:
            keys = {}
            for key in incoming:
                if not isinstance(key, (str, int, float, bool)) and key is not None:
                    raise TypeError('Observation keys must be JSON scalars')
                if type(key) not in (str, int, float, bool, type(None)):
                    return structural_digest(value)
                name = key if isinstance(key, str) else json.dumps(key, allow_nan=False)
                keys[name] = key
            names = tuple(sorted(keys))
            self._mapping_keys = incoming if strings else None
            self.counts['digest_mapping_schema_builds'] += 1
        shape = 'object', names
        self._expand()
        if self.shape != shape:
            # Reuse surviving branches when request context/membership changes.
            old = dict(zip(self.shape[1], self.children)) if (
                isinstance(self.shape, tuple) and self.shape[0] == 'object') else {}
            self.children = tuple(old[name] if name in old else DigestNode(self.counts) for name in names)
            self.shape, self.parts, self.token = shape, None, None
            self._frozen = None
        parts = (tuple(child.update(value[name]) for name, child in zip(names, self.children))
                 if keys is None else
                 tuple(child.update(value[keys[name]]) for name, child in zip(names, self.children)))
        if self.parts != parts:
            self.digest, self.parts = object_digest(names, parts), parts
            self._frozen = None
            self.counts['digest_branches_rehashed'] += 1
        return self.digest


def _live_reason(row, reason):
    row.view.state._observation_readers.digest_counts['digest_live_reason_' + reason] += 1
    return None


def native_card_header(row):
    """Prove a native unmodified row from current inputs, or request a full read.

This is a content guard, not an epoch used as identity. It observes direct writes,
rollback, viewer permissions and mutable ability/effect metadata through the
existing dependency-validated field cache. Modified/custom cards stay live.
"""
    from game.game_state.card import Card, CardDefinition, CardRuntimeState
    from game.game_state.native_modifier_plan import live_sources_empty, tracked_native_sources
    view, card = row.view, row.card
    state, builder = view.state, view.builder
    if not view._native_digest_readers: return _live_reason(row, 'reader_contract')
    if type(card) is not Card: return _live_reason(row, 'card_type')
    if type(card.definition) is not CardDefinition: return _live_reason(row, 'definition_type')
    if type(card.state) is not CardRuntimeState: return _live_reason(row, 'runtime_type')
    if not CARD_READER_NAMES.isdisjoint(vars(card)): return _live_reason(row, 'instance_override')
    if state._refreshing_effects or state._layer_ceiling is not None: return _live_reason(row, 'layer_probe')
    if not builder.knows_identity(card): return _live_reason(row, 'identity_masked')
    if not tracked_native_sources(card): return _live_reason(row, 'untracked_sources')
    if not live_sources_empty(card):
        if card.state.active_cont_effects: return _live_reason(row, 'continuous_effects')
        if card.state.counters: return _live_reason(row, 'counters')
        if card.state.attached: return _live_reason(row, 'attachments')
        return _live_reason(row, 'other_sources')
    if not state._card_snapshots.has_immutable_inputs(card): return _live_reason(row, 'base_stats_unproven')
    # Six characteristics have already proven immutable native inputs. Keep the
    # cached input table alive and compare by identity, never by recyclable ID.
    base = row.base_characteristics()
    runtime = card.state
    primitives = (card.command_id, card.zone_revision, card.owner.idx, row['controller'],
        row['zone'], runtime.face_down, card.is_tapped, runtime.damage_marked,
        runtime.damage_by_deathtouch, card.name, card.is_token, card.definition.legendary,
        card.definition.oracle_text, runtime.mana_x, row['summoning_sick'],
        row['loyalty_activated_this_turn'])
    if any(type(value) not in allowed for value, allowed in zip(primitives, PRIMITIVE_TYPES)):
        return _live_reason(row, 'scalar_type')
    return card.stats, base, primitives


def native_card_parts(row):
    """Exact variable-field identities after the native header proof."""
    # These outputs may depend on nested mutable definitions, live permissions,
    # mana symbols or attachment rules. Their own readers validate those inputs.
    from game.ai.information.runtime_observation import _session
    session = _session.get()
    if session is not None and session.field_groups:
        from game.ai.experiments.runtime_card_fields import (RULE_FIELDS, rule_field_parts)
        rules = iter(rule_field_parts(row))
        parts = tuple(next(rules) if key in RULE_FIELDS else structural_digest(row[key])
                      for key in VARIABLE_FIELDS)
    else:
        parts = tuple(structural_digest(row[key]) for key in VARIABLE_FIELDS)
    return parts


def native_card_inputs(row):
    header = native_card_header(row)
    return None if header is None else (*header, native_card_parts(row))


def native_card_digest(row, inputs):
    """Compose the exact row identity from proven groups without rereading it."""
    parts = card_base_parts(row, inputs)
    values = dict(zip(PRIMITIVE_FIELDS, (structural_digest(v) for v in inputs[2])))
    values.update(zip(BASE_FIELDS, parts))
    values.update(zip(VARIABLE_FIELDS, inputs[3]))
    values.update(FIXED_PARTS)
    return object_digest(CARD_FIELDS, tuple(values[name] for name in CARD_FIELDS))


# Check class overrides once per read lease and instance overrides per card.
# A complete live read remains authoritative for extension/instrumentation paths.
from game.game_state.card import Card
from game.ai.information.observation import PlayerObservationBuilder

CARD_READERS = {name: getattr(Card, name) for name in (
    'get_stat', '_get_modifier_pipeline', 'get_controller', 'get_zone', 'get_types',
    'get_subtypes', 'get_power', 'get_toughness', 'get_mana_fields', 'get_mana_cost',
    'get_casting_cost', 'get_mana_value', 'get_ability_defs', 'get_trigger_defs',
    'modifiers_to_attach', 'is_summoning_sick', 'has_keyword', 'name', 'is_token',
    'is_tapped', 'owner', 'stats', 'state', 'attached_to', 'definition')}
CARD_READER_NAMES = frozenset(CARD_READERS)
BUILDER_READERS = {name: getattr(PlayerObservationBuilder, name) for name in (
    '_base_characteristics', 'knows_identity', 'reference', 'is_visible')}


def native_readers():
    from game.ai.information.runtime_observation import CardReadContract
    return (tuple(sorted(CardReadContract.shell + CardReadContract.identity)) == CARD_FIELDS
            and all(getattr(Card, name) is method for name, method in CARD_READERS.items())
            and all(getattr(PlayerObservationBuilder, name) is method for name, method in BUILDER_READERS.items()))
