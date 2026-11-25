"""Reusable preimages of an owned native simulation graph.

This is a root savepoint, not a per-operation undo journal. Capture once, then
restore original objects after each replay/continuous suffix. Native callbacks
must obey the same no mutable globals / external effects contract as RuntimeClone.
Unknown extension objects/functions fail admission before the worker is run.
"""
from collections import Counter, OrderedDict, defaultdict, deque
from enum import Enum
from gc import get_referents
from random import Random
from types import (
    BuiltinFunctionType, CellType, FunctionType, MappingProxyType,
    MemberDescriptorType, MethodType,
)
import weakref
from helper.static_definition import is_static_definition

from game.ai.simulation.runtime_clone import UnsupportedSnapshot


_MISSING = object()
_ATOMS = {str, int, float, bool, bytes, complex, type(None), object, range}
_CONTAINERS = {tuple, list, dict, set, frozenset, deque, bytearray,
               OrderedDict, Counter, defaultdict}
_STABLE_HASH = {str, int, float, bool, bytes, complex, type(None)}


def _same_mapping(value, saved):
    """Skip writes only with identical order/edges and intrinsically stable keys.

    Runtime keys may have been removed/reinserted under a different hash during
    the branch. Even when their identities match, rebuild those hash tables.
    """
    if len(value) != len(saved):
        return False
    items = value.items() if isinstance(value, OrderedDict) else dict.items(value)
    for (key, item), (old_key, old_item) in zip(items, saved.items()):
        if key is not old_key or item is not old_item or type(key) not in _STABLE_HASH:
            return False
    return True


def _same_sequence(value, saved):
    if len(value) != len(saved):
        return False
    for item, old in zip(value, saved):
        if item is not old:
            return False
    return True


class WorldSavepoint:
    """Own shallow preimages, preserving aliases, identities and RNG state.

    One mark belongs to one private worker. Objects allocated during a branch
    disappear from the worker when its original edges/contents are restored.
    The detached search outputs may retain their own new immutable objects.
    """

    def __init__(self, root, *, preserve_digest_seeds=False):
        from game.ai.information.runtime_digest import DigestSeedBank
        self.attributes, self.slots, self.cells = [], [], []
        self.containers, self.randoms, self.functions = [], [], []
        self.attribute_ids = set()
        seen, pending = set(), [root]
        while pending:
            value = pending.pop()
            cls = type(value)
            if cls in _ATOMS or isinstance(value, (type, Enum)) or cls.__module__ == 'typing':
                continue
            if is_static_definition(value):
                continue
            if preserve_digest_seeds and cls is DigestSeedBank:
                # Reviewed bounded data-only memo; every consumer revalidates
                # its inputs. It is explicitly shared by trusted world copies.
                continue
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            if cls is weakref.ReferenceType:
                target = value()
                if target is not None:
                    pending.append(target)
                continue
            if cls is MethodType:
                pending.extend((value.__self__, value.__func__))
                continue
            if cls is FunctionType:
                if not value.__module__.startswith(('game.', 'helper.')):
                    raise UnsupportedSnapshot('worker callback: ' + value.__qualname__)
                saved = value.__defaults__, value.__kwdefaults__, value.__annotations__
                self.functions.append((value, saved))
                pending.extend((*saved, *(value.__closure__ or ())))
                # Function attributes are mutable and may be aliased too.
            elif cls is CellType:
                try:
                    saved = value.cell_contents
                except ValueError:
                    saved = _MISSING
                self.cells.append((value, saved))
                if saved is not _MISSING:
                    pending.append(saved)
                continue
            elif cls is BuiltinFunctionType:
                owner = value.__self__
                if type(owner) not in (list, dict, set, deque, bytearray):
                    raise UnsupportedSnapshot('worker builtin callback')
                pending.append(owner)
                continue
            elif cls is MappingProxyType:
                # Walking values alone misses mutations of a proxy's backing
                # dictionary when that dictionary has no other incoming edge.
                backing = get_referents(value)
                if len(backing) != 1 or not isinstance(backing[0], dict):
                    raise UnsupportedSnapshot('worker mapping proxy backing')
                pending.extend(backing)
                continue
            elif isinstance(value, Random):
                if cls is not Random:
                    raise UnsupportedSnapshot('worker RNG subclass')
                self.randoms.append((value, value.getstate()))
            else:
                if cls not in _CONTAINERS and not cls.__module__.startswith(('game.', 'helper.', 'immutabledict')):
                    raise UnsupportedSnapshot('worker object: ' + cls.__module__ + '.' + cls.__name__)
                if isinstance(value, dict):
                    saved = dict(value.items()) if isinstance(value, OrderedDict) else dict(dict.items(value))
                    self.containers.append(('ordered' if isinstance(value, OrderedDict) else 'dict', value, saved))
                    pending.extend(saved.keys())
                    pending.extend(saved.values())
                    if isinstance(value, defaultdict):
                        self.slots.append((value, defaultdict.default_factory, value.default_factory))
                        pending.append(value.default_factory)
                elif isinstance(value, list):
                    saved = list(value)
                    self.containers.append(('list', value, saved))
                    pending.extend(saved)
                elif isinstance(value, set):
                    saved = set(value)
                    self.containers.append(('set', value, saved))
                    pending.extend(saved)
                elif isinstance(value, deque):
                    saved = tuple(value)
                    self.containers.append(('deque', value, saved))
                    pending.extend(saved)
                elif isinstance(value, bytearray):
                    self.containers.append(('bytes', value, bytes(value)))
                elif isinstance(value, (tuple, frozenset)):
                    pending.extend(value)
            attributes = getattr(value, '__dict__', None)
            if attributes is not None:
                self.attributes.append((value, attributes))
                self.attribute_ids.add(id(attributes))
                pending.append(attributes)
            if cls is FunctionType:
                continue  # Code/globals/builtins are outside owned callback state.
            for base in cls.__mro__:
                for descriptor in vars(base).values():
                    if type(descriptor) is MemberDescriptorType:
                        try:
                            saved = descriptor.__get__(value, cls)
                        except AttributeError:
                            saved = _MISSING
                        self.slots.append((value, descriptor, saved))
                        if saved is not _MISSING:
                            pending.append(saved)
        self.object_count = len(seen)

    def rollback(self):
        # Restore bindings and mutable key fields before rebuilding hash tables.
        # Reattach the original __dict__, not merely the current dictionary.
        for value, attributes in self.attributes:
            if object.__getattribute__(value, '__dict__') is not attributes:
                object.__setattr__(value, '__dict__', attributes)
        for value, descriptor, saved in self.slots:
            if saved is _MISSING:
                try:
                    descriptor.__delete__(value)
                except AttributeError:
                    pass
            else:
                descriptor.__set__(value, saved)
        for value, saved in self.cells:
            if saved is _MISSING:
                try:
                    del value.cell_contents
                except ValueError:
                    pass
            else:
                value.cell_contents = saved
        for value, saved in self.functions:
            value.__defaults__, value.__kwdefaults__, value.__annotations__ = saved
        for kind, value, saved in self.containers:
            if id(value) in self.attribute_ids:
                if not _same_mapping(value, saved):
                    dict.clear(value)
                    dict.update(value, saved)
            elif kind == 'list':
                if not _same_sequence(value, saved):
                    list.__setitem__(value, slice(None), saved)
            elif kind == 'deque':
                if not _same_sequence(value, saved):
                    deque.clear(value)
                    deque.extend(value, saved)
            elif kind == 'bytes':
                if value != saved:
                    bytearray.__setitem__(value, slice(None), saved)
        for kind, value, saved in self.containers:
            if id(value) in self.attribute_ids:
                continue
            if kind == 'dict':
                if not _same_mapping(value, saved):
                    dict.clear(value)
                    dict.update(value, saved)
            elif kind == 'ordered':
                if not _same_mapping(value, saved):
                    OrderedDict.clear(value)
                    OrderedDict.update(value, saved)
            elif kind == 'set':
                # Mutable runtime keys still require a rebuild after their
                # hash fields are restored. Pure scalar sets have stable hashes.
                if not (all(type(item) in _STABLE_HASH for item in value)
                        and _same_sequence(value, saved)):
                    set.clear(value)
                    set.update(value, saved)
        for value, saved in self.randoms:
            value.setstate(saved)
