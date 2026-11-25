"""Publication of context-free rule data, separate from bound runtime objects.

Normal mutation is rejected after publication. As with frozen dataclasses,
object.__setattr__ and other reflective Python escape hatches are not a sandbox.
Callbacks must obey the engine's existing no mutable-global-runtime contract.
"""
from abc import ABCMeta
from collections.abc import Mapping
from copyreg import _slotnames
from dataclasses import FrozenInstanceError
from enum import Enum
from types import CellType, FunctionType, MethodType
import weakref

from immutabledict import immutabledict


class BoundDefinitionError(TypeError):
    """A supposed static rule contains runtime state or unsupported metadata."""


class RuntimeBinding:
    """Explicitly world-owned executable/bound data, never catalog metadata."""


_published = {}
_building = set()


def is_static_definition(value):
    witness = _published.get(id(value))
    return witness is not None and witness() is value


def _remember(value):
    identity = id(value)
    try:
        _published[identity] = weakref.ref(value, lambda ref: _published.pop(identity, None))
    except TypeError:
        pass


class _DefinitionMeta(ABCMeta):
    def __call__(cls, *args, **kwargs):
        value = super().__call__(*args, **kwargs)
        if id(value) in _building:
            return value
        # Frequently created numeric modifiers need no graph normalizer.
        # Slots and runtime bindings retain the complete structural check.
        params = getattr(cls, '__dataclass_params__', None)
        if (not isinstance(value, RuntimeBinding) and not _slotnames(cls)
                and (cls.__module__.startswith(('game.', 'helper.'))
                     or (params is not None and params.frozen))
                and all(type(item) in (str, int, float, bool, bytes, complex, type(None))
                        or isinstance(item, (Enum, type)) for item in vars(value).values())):
            _remember(value)
            return value
        try:
            publish_definition(value)
        except BoundDefinitionError:
            # E.g. SetModifier(player), EqQuery(..., card), or a runtime duration.
            # It is a world-owned binding, never eligible for shared clone data.
            if getattr(cls, '__static_root__', False):
                binding = vars(cls).get('__runtime_binding_type__')
                if binding is None:
                    raise
                result = object.__new__(binding)
                result.__dict__.update(object.__getattribute__(value, '__dict__'))
                return result
        return value


class StaticDefinition(metaclass=_DefinitionMeta):
    """Rule recipes publish immutable inputs; bound variants remain world-owned."""
    def __setattr__(self, name, value):
        if is_static_definition(self):
            raise FrozenInstanceError(f'Cannot change published {type(self).__name__}.{name}')
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if is_static_definition(self):
            raise FrozenInstanceError(f'Cannot change published {type(self).__name__}.{name}')
        object.__delattr__(self, name)

    def __copy__(self):
        if is_static_definition(self):
            return self
        from copy import _reconstruct
        reduced = self.__reduce_ex__(4)
        return self if isinstance(reduced, str) else _reconstruct(self, {}, *reduced)

    def __deepcopy__(self, memo):
        if is_static_definition(self):
            memo[id(self)] = self
            return self
        from copy import _reconstruct, deepcopy
        reduced = self.__reduce_ex__(4)
        return self if isinstance(reduced, str) else _reconstruct(self, memo, *reduced, deepcopy=deepcopy)


def publish_definition(root):
    """Normalize and seal a fresh root, detaching mutable authoring inputs.

    Reject runtime entities, operations and bound callbacks instead of silently
    sharing their references. Child shells preserve exact public class dispatch.
    """
    if is_static_definition(root):
        return root
    memo, pending, visiting = {}, [], set()

    def freeze(value, path):
        cls, identity = type(value), id(value)
        if isinstance(value, RuntimeBinding):
            raise BoundDefinitionError(f'{path}: bound runtime {cls.__name__}')
        if value is None or cls in (str, int, float, bool, bytes, complex) or isinstance(value, (Enum, type)):
            return value
        if is_static_definition(value):
            return value
        if identity in visiting:
            raise BoundDefinitionError(f'{path}: recursive rule data')
        if identity in memo:
            return memo[identity]
        visiting.add(identity)
        try:
            if isinstance(value, Mapping):
                pairs = {freeze(k, path+'.key'): freeze(v, path+'.value') for k, v in value.items()}
                if isinstance(value, immutabledict):
                    result = cls(pairs)
                elif cls.__module__ == 'game.mana.mana_value' and cls.__name__ == 'ManaValue':
                    result = value.to_immutable().__class__(pairs)
                else:
                    result = immutabledict(pairs)
            elif isinstance(value, (set, frozenset)):
                result = frozenset(freeze(v, path+'[]') for v in value)
                if cls.__module__ == 'helper.mutability_objs' and cls.__name__ == 'ImmutableSet':
                    result = cls(result)
            elif isinstance(value, (tuple, list)):
                result = tuple(freeze(v, path+'[]') for v in value)
                if cls.__module__ == 'helper.mutability_objs' and cls.__name__ == 'ImmutableSequence':
                    result = cls(result)
            elif cls is FunctionType:
                cells = tuple(CellType() for _ in (value.__closure__ or ()))
                result = FunctionType(value.__code__, value.__globals__, value.__name__, closure=cells or None)
                memo[identity] = result
                for old, new in zip(value.__closure__ or (), cells):
                    try:
                        captured = old.cell_contents
                    except ValueError:
                        continue
                    if isinstance(captured, (list, dict, set)):
                        raise BoundDefinitionError(f'{path}: mutable callback capture requires runtime binding')
                    new.cell_contents = freeze(captured, path+'.closure')
                result.__defaults__ = freeze(value.__defaults__, path+'.defaults')
                result.__kwdefaults__ = (None if value.__kwdefaults__ is None else
                    dict(freeze(value.__kwdefaults__, path+'.kwdefaults')))
                result.__qualname__, result.__module__ = value.__qualname__, value.__module__
                result.__annotations__ = dict(freeze(value.__annotations__, path+'.annotations'))
                if value.__dict__:
                    raise BoundDefinitionError(f'{path}: callback attributes require an explicit recipe')
                pending.append(result)
            elif cls is MethodType:
                owner = freeze(value.__self__, path+'.owner')
                result = MethodType(freeze(value.__func__, path+'.function'), owner)
            else:
                params = getattr(cls, '__dataclass_params__', None)
                if (isinstance(value, StaticDefinition) and not (params is not None and params.frozen)
                        and not cls.__module__.startswith(('game.', 'helper.'))):
                    raise BoundDefinitionError(f'{path}: extension runtime recipe requires explicit immutable data')
                if not isinstance(value, StaticDefinition) and not (params is not None and params.frozen):
                    raise BoundDefinitionError(f'{path}: runtime/unsupported {cls.__module__}.{cls.__name__}')
                # Bound definitions are frozen records too; their Card/Player,
                # operation or mutable state descendants still fail this walk.
                result = root if value is root else object.__new__(cls)
                memo[identity] = result
                attributes = object.__getattribute__(value, '__dict__') if hasattr(value, '__dict__') else {}
                converted = {name: freeze(item, path+'.'+name) for name, item in attributes.items()}
                slots = {name: freeze(getattr(value, name), path+'.'+name)
                         for name in (_slotnames(cls) or ()) if hasattr(value, name)}
                for name, item in (*converted.items(), *slots.items()):
                    object.__setattr__(result, name, item)
                pending.append(result)
            memo[identity] = result
            return result
        finally:
            visiting.remove(identity)

    _building.add(id(root))
    try:
        result = freeze(root, type(root).__name__)
        for value in pending:
            _remember(value)
        return result
    finally:
        _building.discard(id(root))
