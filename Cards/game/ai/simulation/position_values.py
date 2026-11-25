"""Narrow immutable values reusable inside one stable-handle position recipe.

Values are selected from the already detached recipe, never from another
sampled world. Mutable runtime records, LKI and arbitrary callbacks stay copied.
"""
from dataclasses import fields
from enum import Enum
from types import MethodType, FunctionType

from game.ai.information.knowledge import PlayerEvent
from game.game_state.modifier import TimeStamp, GameMoment


_FIELDS = {cls: tuple(f.name for f in fields(cls)) for cls in (PlayerEvent, TimeStamp, GameMoment)}
_SCALARS = frozenset((str, int, float, bool, bytes, type(None)))


def root_value_kind(value, handles):
    """Return a proof category; a frozen outer class alone is insufficient."""
    cls = type(value)
    if cls is MethodType:
        owner, function = value.__self__, value.__func__
        if (handles.get(id(owner)) is owner and type(function) is FunctionType and not function.__closure__
                and not function.__defaults__ and not function.__kwdefaults__):
            return 'stable_methods'
        return None
    names = _FIELDS.get(cls)
    if names is not None:
        attrs = vars(value)
        if (tuple(attrs) == names
                and all(type(item) in _SCALARS or isinstance(item, Enum) for item in attrs.values())):
            return cls.__name__
    return None


def recipe_constants(clone, handles):
    """Classify copied results, so publication never shares the pre-copy source."""
    stable = {id(handle): handle for handle in handles}
    results = {id(clone.memo[id(original)]): clone.memo[id(original)]
               for original in clone._originals if id(original) in clone.memo}
    # A frozen record's attribute dictionary can have an explicit mutable alias
    # elsewhere in an extension graph. Such a record must retain graph-copy
    # semantics; otherwise the alias would detach from the shared record.
    exposed = set()
    for value in results.values():
        cls = type(value)
        if cls is dict:
            children = value.values()
        elif cls in (tuple, list):
            children = value
        elif cls is MethodType:
            children = (value.__self__,)
        elif cls is FunctionType:
            children = [value.__defaults__, value.__kwdefaults__]
            for cell in value.__closure__ or ():
                try:
                    children.append(cell.cell_contents)
                except ValueError:
                    pass
        else:
            continue
        exposed.update(id(item) for item in children if type(item) is dict)
    constants, counts = {}, {}
    for value in results.values():
        kind = root_value_kind(value, stable)
        if kind is not None:
            if kind != 'stable_methods' and id(vars(value)) in exposed:
                continue
            constants[id(value)] = value
            counts[kind] = counts.get(kind, 0) + 1
    return constants, counts
