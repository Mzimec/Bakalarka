"""Immutable JSON-shaped facts, shared within one bounded projection store.

No engine objects are accepted. Text JSON is a lazy export/compatibility view;
simulation hashing and equality use typed immutable structure instead.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from hashlib import blake2b
import json
import math
import marshal
from types import MappingProxyType
from sys import getsizeof


@dataclass(frozen=True)
class Value:
    """!
    @brief Typed immutable JSON-shaped fact with a digest and lazy derived views.
    @var kind Explicit record/action kind used by this protocol.
    @var items Immutable scalar, array entries or sorted object fields.
    @var digest Typed structural digest of the immutable facts.
    @var _hash Hash retained by this record.
    """
    kind: str
    items: object
    digest: bytes
    _hash: int = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, '_hash', int.from_bytes(self.digest[:8], 'little', signed=True))

    def __hash__(self):
        return self._hash

    def thaw(self):
        """!
        @brief Create an independently mutable JSON-shaped export of immutable facts.
        """
        if self.kind == 'object':
            return {key: value.thaw() for key, value in self.items}
        if self.kind == 'array':
            return [value.thaw() for value in self.items]
        return self.items

    @cached_property
    def readonly(self):
        """!
        @brief Lazy internal view: share frozen subtrees instead of deep thawing.

        Object fields are resolved on access; arrays are immutable tuples.
        Public decode()/thaw() still return independently mutable JSON data.
        """
        if self.kind == 'object':
            return ReadOnlyObject(self)
        if self.kind == 'array':
            return tuple(value.readonly for value in self.items)
        return self.items

    def __reduce__(self):
        # Derived views/indexes must not inflate worker payloads. In particular
        # a read-only mapping points back to this Value; serialize facts only.
        return Value, (self.kind, self.items, self.digest)

    @cached_property
    def order(self):
        """!
        @brief Expose lazy canonical lexical comparison for deterministic tie breaks.
        """
        return LexicalValue(self)

    @cached_property
    def fragments(self):
        # Match the legacy canonical JSON tie break without constructing the
        # complete document. Equal immutable subtrees are skipped as units.
        """!
        @brief Expose canonical JSON comparison fragments over shared immutable subtrees.
        """
        if self.kind == 'object':
            result = ['{']
            for index, (key, value) in enumerate(self.items):
                if index:
                    result.append(',')
                result.extend((json.encoder.encode_basestring_ascii(key), ':', value))
            return (*result, '}')
        if self.kind == 'array':
            result = ['[']
            for index, value in enumerate(self.items):
                if index:
                    result.append(',')
                result.append(value)
            return (*result, ']')
        if self.kind == 'str':
            return (json.encoder.encode_basestring_ascii(self.items),)
        if self.items is None:
            return ('null',)
        if self.kind == 'bool':
            return ('true' if self.items else 'false',)
        return (repr(self.items),)


@dataclass(frozen=True)
class LexicalValue:
    """!
    @brief Compare immutable facts in canonical JSON tie-break order.
    @var value Value retained by this record.
    """
    value: Value

    def __lt__(self, other):
        left, right = [self.value], [other.value]
        while left and right:
            a, b = left[-1], right[-1]
            if a is b or a == b:
                left.pop()
                right.pop()
                continue
            if isinstance(a, Value):
                left.pop()
                left.extend(reversed(a.fragments))
                continue
            if isinstance(b, Value):
                right.pop()
                right.extend(reversed(b.fragments))
                continue
            if a.startswith(b):
                left[-1] = a[len(b):]
                right.pop()
            elif b.startswith(a):
                right[-1] = b[len(a):]
                left.pop()
            else:
                return a < b
        return bool(right) and not left


class ReadOnlyObject(Mapping):
    """!
    @brief Read-only field index over one already visibility-filtered Value.
    """
    __slots__ = ('_value', '_fields', '_index_bytes')

    def __init__(self, value):
        fields = dict(value.items)
        object.__setattr__(self, '_value', value)
        object.__setattr__(self, '_fields', MappingProxyType(fields))
        object.__setattr__(self, '_index_bytes', getsizeof(fields))

    def __sizeof__(self):
        # Tree retention traverses _value but cannot traverse a proxy's backing
        # dict. Charge the field index too, so derived views respect its cap.
        return object.__sizeof__(self) + self._index_bytes

    def __setattr__(self, name, value):
        raise AttributeError('Structured read view is immutable')

    def __getitem__(self, key):
        return self._fields[key].readonly

    def get(self, key, default=None):
        """!
        @brief Look up a cached result and update the owner access accounting.
        """
        value = self._fields.get(key)
        return default if value is None else value.readonly

    def __iter__(self):
        return iter(self._fields)

    def __len__(self):
        return len(self._fields)

    def __reduce__(self):
        return ReadOnlyObject, (self._value,)


class ValueStore:
    """!
    @brief Bound and share validated immutable visible facts by structure and content.
    """
    def __init__(self, limit=8192):
        if type(limit) is not int or limit < 0:
            raise ValueError('Structured value limit must be nonnegative')
        self.limit, self.rows, self.hits = limit, {}, 0
        self.binary_rows, self.binary_bytes, self.binary_hits = {}, 0, 0

    def freeze(self, value):
        """!
        @brief Validate and intern detached facts without retaining native runtime objects.
        """
        value_type = type(value)
        if value_type is ReadOnlyObject:
            return value._value
        blob = None
        if self.limit and value_type is dict and len(value) >= 6:
            # C-level content lookup skips rewalking unchanged card/rule rows.
            # Cache keys are bytes, never live dicts. A miss still goes through
            # the strict JSON-type validator below before it can be cached.
            try:
                # Version 2 omits reference-count/alias-dependent flags, so
                # equivalent fresh rows have the same content bytes.
                blob = marshal.dumps(value, 2)
            except (ValueError, TypeError):
                pass
            if blob is not None and blob in self.binary_rows:
                self.binary_hits += 1
                return self.binary_rows[blob]
        if value is None or value_type in (str, int, float, bool):
            if value_type is float and not math.isfinite(value):
                raise ValueError('Observation numbers must be finite')
            kind, items = value_type.__name__, value
        elif isinstance(value, StructuredJSON):
            return value.value
        elif isinstance(value, Value):
            return value
        elif isinstance(value, Mapping):
            # Match JSON object-key normalization and last-key-wins semantics.
            normalized = {}
            for key, item in value.items():
                if not isinstance(key, (str, int, float, bool)) and key is not None:
                    raise TypeError('Observation keys must be JSON scalars')
                key = key if isinstance(key, str) else json.dumps(key, allow_nan=False)
                normalized[key] = self.freeze(item)
            kind, items = 'object', tuple(sorted(normalized.items()))
        elif isinstance(value, (list, tuple)):
            kind, items = 'array', tuple(self.freeze(item) for item in value)
        else:
            raise TypeError(f'Observation contains a runtime object: {type(value).__name__}')
        # JSON distinguishes -0.0 from 0.0 even though Python equality does not.
        key = kind, repr(items) if kind == 'float' else items
        found = self.rows.get(key)
        if found is not None:
            self.hits += 1
            if blob is not None:
                self._remember_binary(blob, found)
            return found
        digest = blake2b(digest_size=20, person=b'card-search-v1')
        digest.update(kind.encode())
        if kind == 'object':
            for name, item in items:
                encoded = name.encode('utf-8', 'surrogatepass')
                digest.update(len(encoded).to_bytes(8, 'little'))
                digest.update(encoded)
                digest.update(item.digest)
        elif kind == 'array':
            for item in items:
                digest.update(item.digest)
        else:
            digest.update(repr(items).encode('utf-8'))
        result = Value(kind, items, digest.digest())
        if self.limit:
            if len(self.rows) >= self.limit:
                # Old records own their values; eviction cannot invalidate them.
                self.rows.clear()
            self.rows[key] = result
        if blob is not None:
            self._remember_binary(blob, result)
        return result

    def _remember_binary(self, blob, value):
        if self.binary_bytes + len(blob) > 8 * 1024 * 1024:
            self.binary_rows.clear()
            self.binary_bytes = 0
        if len(blob) <= 8 * 1024 * 1024 and blob not in self.binary_rows:
            self.binary_rows[blob] = value
            self.binary_bytes += len(blob)


class StructuredJSON:
    """!
    @brief Private lazy payload; dataclass exports retain the original JSON schema.
    """
    def __init__(self, value, store=None):
        object.__setattr__(self, 'value', (store or ValueStore(0)).freeze(value))

    def __setattr__(self, name, value):
        raise AttributeError('Structured search payload is immutable')

    @cached_property
    def text(self):
        """!
        @brief Materialize compatible JSON or chained digest text only when requested.
        """
        return json.dumps(self.value.thaw(), sort_keys=True, separators=(',', ':'), allow_nan=False)

    def __deepcopy__(self, memo):
        return self.text

    def __repr__(self):
        return f'StructuredJSON({self.value.digest.hex()})'

    def __contains__(self, item):
        return item in self.text

    def __eq__(self, other):
        return self.value == other.value if isinstance(other, StructuredJSON) else self.text == other

    def __hash__(self):
        return hash(self.text)


def payload(value):
    """!
    @brief Expose immutable structured facts from structured or compatible text input.
    """
    return value if isinstance(value, StructuredJSON) else StructuredJSON(json.loads(value))


def decode(value):
    """!
    @brief Return independently mutable JSON data from a player-facing payload.
    """
    return value.value.thaw() if isinstance(value, StructuredJSON) else json.loads(value)


def text(value):
    """!
    @brief Materialize compatible JSON or chained digest text only when requested.
    """
    return value.text if isinstance(value, StructuredJSON) else value


def structural_key(*values):
    # Key construction needs a digest, not another allocated immutable tree.
    # Preserve exactly ValueStore's typed digest, including already frozen rows.
    """!
    @brief Hash typed immutable structure without materializing complete JSON text.
    """
    def digest(value):
        if isinstance(value, StructuredJSON):
            return value.value.digest
        if isinstance(value, Value):
            return value.digest
        value_type = type(value)
        if value is None or value_type in (str, int, float, bool):
            if value_type is float and not math.isfinite(value):
                raise ValueError('Observation numbers must be finite')
            result = blake2b(digest_size=20, person=b'card-search-v1')
            result.update(value_type.__name__.encode())
            result.update(repr(value).encode('utf-8'))
        elif value_type in (list, tuple):
            result = blake2b(digest_size=20, person=b'card-search-v1')
            result.update(b'array')
            for child in value:
                result.update(digest(child))
        else:
            return ValueStore(0).freeze(value).digest
        return result.digest()
    return digest(values).hex()


class RecallDigest:
    """!
    @brief O(1) append identity; legacy chained SHA text is materialized on export.
    """
    def __init__(self, previous, kind, entry):
        object.__setattr__(self, 'previous', previous)
        object.__setattr__(self, 'kind', kind)
        object.__setattr__(self, 'entry', entry)
        previous_fast = previous.fast if isinstance(previous, RecallDigest) else previous
        object.__setattr__(self, 'fast', structural_key(previous_fast, kind, entry))

    def __setattr__(self, name, value):
        raise AttributeError('Recall digest is immutable')

    @cached_property
    def text(self):
        """!
        @brief Materialize compatible JSON or chained digest text only when requested.
        """
        from hashlib import sha256
        trail, cursor = [], self
        while isinstance(cursor, RecallDigest) and 'text' not in cursor.__dict__:
            trail.append(cursor)
            cursor = cursor.previous
        digest = cursor.__dict__['text'] if isinstance(cursor, RecallDigest) else cursor
        for item in reversed(trail):
            encoded = json.dumps((digest, item.kind, text(item.entry)), separators=(',', ':'), ensure_ascii=True)
            digest = sha256(encoded.encode()).hexdigest()
            item.__dict__['text'] = digest
        return digest

    def __deepcopy__(self, memo):
        return self.text

    def __reduce__(self):
        # Pickle's default object traversal recurses through every previous
        # digest. Long games must travel to spawned workers without depending
        # on Python's recursion limit or eagerly exporting legacy JSON hashes.
        rows, cursor = [], self
        while isinstance(cursor, RecallDigest):
            rows.append((cursor.kind, cursor.entry, cursor.fast, cursor.__dict__.get('text')))
            cursor = cursor.previous
        return _restore_recall_digest, (cursor, tuple(reversed(rows)))

    def __eq__(self, other):
        return self.fast == other.fast if isinstance(other, RecallDigest) else self.text == other

    def __hash__(self):
        return hash(self.text)

    def __repr__(self):
        return f'RecallDigest({self.fast})'


def _restore_recall_digest(anchor, rows):
    previous = anchor
    for kind, entry, fast, cached_text in rows:
        value = object.__new__(RecallDigest)
        value.__dict__.update(previous=previous, kind=kind, entry=entry, fast=fast)
        if cached_text is not None:
            value.__dict__['text'] = cached_text
        previous = value
    return previous
