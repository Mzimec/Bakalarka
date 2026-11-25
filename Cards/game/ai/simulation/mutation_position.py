"""Hybrid first-write records plus complete fallback preimages for one worker.

Only reviewed native record writers may skip eager restoration. Unknown rule
graphs fail admission. This is an explicit pilot, not a generic Python undo.
"""
from collections import Counter, deque
from enum import Enum
from pathlib import Path
from random import Random
from types import (FunctionType, MethodType, BuiltinFunctionType, MappingProxyType,
                   MemberDescriptorType)
from functools import cached_property
from time import perf_counter
import functools

from helper.runtime_mutations import MutationLedger, MutationRecord, mutation_scope
from game.ai.simulation.reversible_world import WorldSavepoint, _same_sequence
from game.ai.simulation.runtime_clone import UnsupportedSnapshot


PUBLIC_PLAYER_FIELDS = frozenset(('_health', 'health', '_idx', '_name', 'poison_counters',
    '_poison_counters', 'has_lost', '_has_lost',
    'maximum_hand_size', 'lands_played_this_turn', 'land_plays_per_turn',
    '_zone_map', 'mana_pool'))


def native_rule_graph(root, *, optimized=False):
    """Check actual native files, including callbacks inside published metadata.

    As with RuntimeClone this trusts native engine code and forbids mutable
    external globals. Namespace spoofing is not a security boundary.
    """
    base = Path(__file__).resolve().parents[3]
    prefixes = tuple(str(base / name).replace('\\', '/').lower() + '/' for name in ('game', 'helper'))
    seen, checked_classes, pending = set(), set(), [root]
    slot_plans = {}

    def check_class(cls):
        for parent in cls.__mro__:
            if parent in checked_classes or not parent.__module__.startswith(('game.', 'helper.')):
                continue
            checked_classes.add(parent)
            for name, method in vars(parent).items():
                methods = ((method.fget, method.fset, method.fdel) if isinstance(method, property)
                    else (method.__func__,) if isinstance(method, (classmethod, staticmethod))
                    else (method.func,) if isinstance(method, cached_property) else (method,))
                for fn in methods:
                    fn = getattr(fn, '__runtime_rule_passthrough__', fn)
                    if not isinstance(fn, FunctionType):
                        continue
                    filename = fn.__code__.co_filename.replace('\\', '/').lower()
                    if filename.endswith('/contextlib.py') and isinstance(getattr(fn, '__wrapped__', None), FunctionType):
                        filename = fn.__wrapped__.__code__.co_filename.replace('\\', '/').lower()
                    if filename.startswith(prefixes):
                        continue
                    if name in ('__lt__', '__le__', '__gt__', '__ge__') and any(
                            fn is getattr(functools, key, None) for key in
                            ('_lt_from_le', '_lt_from_gt', '_lt_from_ge', '_le_from_lt',
                             '_le_from_gt', '_le_from_ge', '_gt_from_lt', '_gt_from_le',
                             '_gt_from_ge', '_ge_from_lt', '_ge_from_le', '_ge_from_gt')):
                        continue
                    # Python generates these for native dataclass/namedtuple
                    # records. They are structural operations, not rule hooks.
                    generated = (('__dataclass_fields__' in vars(parent) and name in
                        ('__init__', '__repr__', '__eq__', '__hash__', '__setattr__', '__delattr__',
                         '__lt__', '__le__', '__gt__', '__ge__', '__getstate__', '__setstate__'))
                        or (issubclass(parent, tuple) and hasattr(parent, '_fields')))
                    if generated and (filename == '<string>' or filename.endswith('/dataclasses.py')
                                      or filename.endswith('/collections/__init__.py')):
                        continue
                    raise UnsupportedSnapshot('journal external class method: ' + parent.__qualname__ + '.' + name)
    while pending:
        value = pending.pop()
        cls = type(value)
        if cls in (str, int, float, bool, bytes, complex, type(None), object, range) or isinstance(value, Enum):
            continue
        if id(value) in seen:
            continue
        seen.add(id(value))
        if cls.__module__ == 'typing':
            continue
        if isinstance(value, type):
            if not value.__module__.startswith(('game.', 'helper.', 'builtins', 'typing', 'collections')):
                raise UnsupportedSnapshot('journal external metadata class: ' + value.__qualname__)
            check_class(value)
            continue
        if cls is MethodType:
            pending.extend((value.__self__, value.__func__))
            continue
        if cls is FunctionType:
            filename = value.__code__.co_filename.replace('\\', '/').lower()
            if not filename.startswith(prefixes):
                raise UnsupportedSnapshot('journal external callback: ' + value.__qualname__)
            pending.extend((value.__defaults__, value.__kwdefaults__, vars(value)))
            for cell in value.__closure__ or ():
                try:
                    pending.append(cell.cell_contents)
                except ValueError:
                    pass
            continue
        if cls is BuiltinFunctionType:
            owner = value.__self__
            if getattr(value, '__module__', None) != 'builtins' and type(owner) not in (dict, list, set, deque):
                raise UnsupportedSnapshot('journal external builtin callback')
            continue
        if cls is Random:
            continue
        if isinstance(value, MappingProxyType):
            pending.extend(value.keys())
            pending.extend(value.values())
            continue
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (tuple, list, set, frozenset, deque)):
            pending.extend(value)
        elif not cls.__module__.startswith(('game.', 'helper.', 'immutabledict', 'weakref')):
            raise UnsupportedSnapshot('journal external record: ' + cls.__module__ + '.' + cls.__qualname__)
        check_class(cls)
        pending.extend(getattr(value, '__dict__', {}).values())
        if optimized and cls in slot_plans:
            descriptors = slot_plans[cls]
        else:
            descriptors = tuple(descriptor for parent in cls.__mro__
                for descriptor in vars(parent).values() if type(descriptor) is MemberDescriptorType)
            if optimized:
                slot_plans[cls] = descriptors
        for descriptor in descriptors:
            try:
                pending.append(descriptor.__get__(value, cls))
            except AttributeError:
                pass


class MutationPosition:
    def __init__(self, state, memories, counts, *, validate=False, world_adapters=None):
        from game.game_state.card import CardRuntimeState, ZoneInfo, AttachInfo
        from game.game_state.player import Player
        from game.game_state.battlefield import CardCollection, Battlefield
        from game.mana.mana_value import ManaPool
        from helper.change_tracking import TrackedDict, TrackedSet
        started = perf_counter()
        optimized = world_adapters is not None and world_adapters.optimized
        native_rule_graph((state, memories), optimized=optimized)
        counts['prepare_audit_seconds'] += perf_counter() - started
        self.state, self.counts = state, counts
        readers = getattr(state, '_observation_readers', None)
        self.fields = None if readers is None else readers.fields
        started = perf_counter()
        self.fallback = mark = WorldSavepoint((state, memories), preserve_digest_seeds=True)
        counts['prepare_capture_seconds'] += perf_counter() - started
        self.validation = mark.containers.copy() if validate else ()
        self.validation_attributes = tuple(mark.attributes) if validate else ()
        self.ledger = MutationLedger(counts, owner=state)
        # Only the reviewed native-world adapters lend execution with immutable
        # rule data. External writers and reflective edits during that loan are
        # outside its contract; ordinary metadata writes are already forbidden.
        self.ledger.native_rule_data_loan = world_adapters is not None
        started = perf_counter()
        aliases = Counter()
        immutable_edges = []
        for kind, value, saved in mark.containers:
            values = (*saved.keys(), *saved.values()) if kind in ('dict', 'ordered') else saved
            aliases.update(map(id, values))
            immutable_edges.extend(values)
        aliases.update(id(v) for _, _, v in mark.slots)
        aliases.update(id(v) for _, v in mark.cells)
        for _, saved in mark.functions:
            aliases.update(map(id, saved))
            immutable_edges.extend(saved)
        immutable_edges.extend(v for _, _, v in mark.slots)
        immutable_edges.extend(v for _, v in mark.cells)
        seen_immutable, readonly_proxies = set(), []
        while immutable_edges:
            value = immutable_edges.pop()
            if id(value) in seen_immutable:
                continue
            seen_immutable.add(id(value))
            if isinstance(value, (tuple, frozenset)):
                aliases.update(map(id, value))
                immutable_edges.extend(value)
            elif type(value) is MappingProxyType:
                from gc import get_referents
                backing = get_referents(value)
                if len(backing) == 1 and type(backing[0]) is dict:
                    readonly_proxies.append(backing[0])
        eager = {id(value): (kind, value, saved) for kind, value, saved in mark.containers}
        owners = Counter(id(attributes) for _, attributes in mark.attributes)
        counts['prepare_alias_seconds'] += perf_counter() - started
        started = perf_counter()
        expanded_types = set()
        if world_adapters is not None:
            from game.ai.simulation.world_mutation_contracts import native_record_type
            expanded_types = {type(source) for source, _ in mark.attributes
                if native_record_type(type(source)) and type(source) not in world_adapters.eager_types}
            world_adapters.attributes(expanded_types)
        removed = set()
        for source, attributes in mark.attributes:
            cls = type(source)
            if cls not in expanded_types and cls not in (CardRuntimeState, ZoneInfo, AttachInfo, Player,
                           CardCollection, Battlefield, ManaPool, TrackedDict, TrackedSet):
                continue
            storage, name = None, None
            if cls in (CardCollection, Battlefield):
                storage = source
            elif cls is TrackedDict:
                storage, name = source.data, 'data'
            elif cls is TrackedSet:
                storage, name = source._values, '_values'
            elif cls is ManaPool:
                storage, name = source._data, '_data'
            attrs_ok = aliases[id(attributes)] == 0 and owners[id(attributes)] == 1
            # Card.__setattr__ can transfer these fields using object.__setattr__
            # when an incomplete compatibility record is installed.
            if cls is CardRuntimeState and '_stats' not in attributes:
                attrs_ok = False
            contents_ok = (storage is not None and id(storage) in eager
                           and (name is None or aliases[id(storage)] == 1))
            if contents_ok and type(storage) not in (dict, set, CardCollection, Battlefield):
                contents_ok = False
            record = MutationRecord(source, attributes, storage, name, attrs_ok, contents_ok,
                                    PUBLIC_PLAYER_FIELDS if cls is Player else None,
                                    track_revisions=not optimized or cls in (CardRuntimeState,ZoneInfo,
                                        AttachInfo,Player,CardCollection,Battlefield,ManaPool,TrackedDict,TrackedSet))
            self.ledger.add(record)
            if attrs_ok:
                removed.add(id(attributes))
            if contents_ok:
                removed.add(id(storage))
        for record in self.ledger.records.values():
            source = record.source
            if type(source) is CardRuntimeState:
                for name in ('counters', 'active_cont_effects', 'attached', 'attach_info', 'zone_info'):
                    self.ledger.link(getattr(source, name), source)
            elif type(source) is Player:
                for child in (source.mana_pool, source.hand, source.deck):
                    self.ledger.link(child, source)
        if world_adapters is not None:
            from game.ai.simulation.world_mutation_contracts import container_fields, walk_containers
            contracts = {}
            for source, attributes in mark.attributes:
                cls = type(source)
                if optimized and cls in contracts:
                    fields, recursive, readonly = contracts[cls]
                else:
                    fields, recursive, readonly = container_fields(source)
                    contracts[cls] = fields, recursive, readonly
                record = self.ledger.record(source)
                if readonly and (record is None or not record.track_attributes):
                    continue
                for name in fields:
                    for value in walk_containers(getattr(source, name, None), recursive=recursive):
                        identity = id(value)
                        if identity in removed or identity in mark.attribute_ids or aliases[identity] != 1:
                            continue
                        entry = eager.get(identity)
                        if entry is None:
                            continue
                        kind = entry[0]
                        self.ledger.add(MutationRecord(value, None, value, track_contents=True,
                            storage_kind=kind,track_revisions=not optimized))
                        removed.add(identity)
                        counts['world_content_roots'] += 1
            counts['world_attribute_roots'] += sum(r.track_attributes for r in self.ledger.records.values())
            # A proxy-only backing has no writable edge in the owned graph.
            # Explicit aliases (also through tuples/defaults/cells) retain full
            # preimages. Native rules may not reach mutable external globals.
            for value in readonly_proxies:
                identity = id(value)
                if identity in eager and identity not in removed and not aliases[identity]:
                    self.ledger.add(MutationRecord(value, None, value, track_contents=True,
                        storage_kind='dict',track_revisions=not optimized))
                    removed.add(identity)
                    counts['world_readonly_proxy_roots'] += 1
        mark.attributes = [(v, a) for v, a in mark.attributes if id(a) not in removed]
        mark.attribute_ids.difference_update(removed)
        mark.containers = [entry for entry in mark.containers if id(entry[1]) not in removed]
        counts['journal_workers'] += 1
        counts['journal_records'] += len(self.ledger.records)
        counts['lazy_root_preimages'] += len(removed)
        counts['fallback_containers'] += len(mark.containers)
        counts['journal_revision_records'] += sum(r.track_revisions for r in self.ledger.records.values())
        counts['journal_undo_records'] += sum(not r.track_revisions for r in self.ledger.records.values())
        counts['prepare_records_seconds'] += perf_counter() - started

    def rollback(self):
        readers = getattr(self.state, '_observation_readers', None)
        if readers is not None and any(v._builder is not None for v in readers.views.values()):
            raise RuntimeError('Cannot roll back a journal during a live observation read')
        with mutation_scope(None):
            # Match the existing position-worker cache contract: published
            # descriptions are bounded and recheck their dependency/permission
            # plans on the next lease. They must not be erased by root undo.
            from helper.static_definition import is_static_definition
            retained = (() if self.fields is None else tuple((key, entry)
                for key, entry in self.fields.entries.items() if is_static_definition(entry[0])))
            # Feed first-write preimages into the SAME two-phase restorer as
            # the fallback. All bindings/key fields must be restored before
            # rebuilding any membership table (including aliased native keys).
            mark = self.fallback
            attributes, containers = len(mark.attributes), len(mark.containers)
            added_ids = []
            for record in self.ledger.dirty_attributes:
                mark.attributes.append((record.source, record.attributes))
                mark.attribute_ids.add(id(record.attributes))
                added_ids.append(id(record.attributes))
                mark.containers.append(('dict', record.attributes, record.attributes_saved))
            for record in self.ledger.dirty_contents:
                mark.containers.append((record.storage_kind or ('set' if type(record.storage) is set else 'dict'),
                                        record.storage, record.contents_saved))
            try:
                mark.rollback()
            finally:
                del mark.attributes[attributes:]
                del mark.containers[containers:]
                mark.attribute_ids.difference_update(added_ids)
            if self.validation:
                for source, attributes in self.validation_attributes:
                    if object.__getattribute__(source, '__dict__') is not attributes:
                        raise AssertionError('Journal writer coverage missed attribute binding')
                for kind, value, saved in self.validation:
                    if kind in ('dict', 'ordered'):
                        current = tuple(value.items() if kind == 'ordered' else dict.items(value))
                        expected = tuple(saved.items())
                        equal = (len(current) == len(expected) and all(
                            a is b and x is y for (a, x), (b, y) in zip(current, expected)))
                    elif kind == 'set':
                        equal = {id(v) for v in value} == {id(v) for v in saved}
                    elif kind == 'bytes':
                        equal = value == saved
                    else:
                        equal = _same_sequence(value, saved)
                    if not equal:
                        owners = [type(source).__qualname__ + '.__dict__'
                                  for source, attributes in self.validation_attributes if attributes is value]
                        owners.extend(type(source).__qualname__ + '.' + name
                            for source, attributes in self.validation_attributes
                            for name, child in attributes.items() if child is value)
                        raise AssertionError('Journal writer coverage missed ' + kind + ' ' + str(type(value))
                                             + ' owners=' + repr(owners)
                                             + ' items=' + str(len(value)) + '/' + str(len(saved)))
            if self.fields is not None:
                self.fields.entries.update(retained)
                self.fields.retain_definitions()
                while len(self.fields.entries) > self.fields.limit:
                    self.fields.entries.popitem(last=False)
                self.counts['retained_definition_entries'] += len(retained)
            self.ledger.restored_root()
