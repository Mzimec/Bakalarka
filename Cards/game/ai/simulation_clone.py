"""Fast ownership copy of trusted simulation graphs after visibility filtering.

Keep RuntimeClone as the filtering/reconstruction backend and correctness
reference. Here most edges are primitive record fields: copy those directly
instead of dispatching through the general graph copier for each key/value.
Mutable references still use a single memo, including closure captures and
extension objects handled by the reference copier.
"""
from functools import lru_cache
from types import BuiltinMethodType, FunctionType, MappingProxyType

from game.ai.runtime_clone import RuntimeClone, _ATOMIC, _plain_record


_VALUES = _ATOMIC - {FunctionType, BuiltinMethodType}


@lru_cache(maxsize=512)
def _live_mapping(cls):
    from helper.dict_helper import MutableCounterMapping, MutableSetMapping
    return issubclass(cls, (MutableCounterMapping, MutableSetMapping))


@lru_cache(maxsize=1)
def _native_containers():
    """!
    @brief Exact reviewed types only; extension serialization stays on RuntimeClone.
    """
    from immutabledict import immutabledict
    from helper.runtime_object import KeyedCollection, ImmutableKeyedCollection
    from helper.mutability_objs import ImmutableDict
    from game.game_state.battlefield import CardCollection, Battlefield
    from game.game_state.card import Card
    from game.game_state.player import Player
    from game.game_actions.data_structs.ability import AbilityCollection
    from game.game_actions.data_structs.action_node import ImmutableEffectToSlotMap
    from game.mana.mana_value import ImmutableManaValue
    return (frozenset((KeyedCollection, CardCollection, Battlefield, AbilityCollection)),
            frozenset((immutabledict, ImmutableKeyedCollection, ImmutableDict,
                       ImmutableManaValue, ImmutableEffectToSlotMap)), frozenset((Card, Player)))


class SimulationClone(RuntimeClone):
    """!
    @brief Copy owned worlds/filtered roots; never perform visibility filtering.
    """

    def __init__(self, replacements=(), *, dictionary_plans=None, object_replacements=None,
                 component_copies=True):
        replacements = dict(replacements)
        # Owned-world callers supply original objects, not only their ids. This
        # proves that no primitive value is substituted. The generic id-based
        # API keeps its original memo lookup contract, including primitive keys.
        self._primitive_identity = False
        self._replacement_objects = ()
        if object_replacements is not None:
            pairs = tuple(object_replacements)
            self._replacement_objects = pairs
            self._primitive_identity = (
                all(identity == id(value) for identity, value in replacements.items())
                and all(type(old) not in _VALUES or old is new for old, new in pairs))
            replacements.update((id(old), new) for old, new in pairs)
        super().__init__(replacements, dictionary_plans=dictionary_plans)
        self.component_copies = component_copies
        self._native_dicts, self._native_frozen_maps, self._native_identities = _native_containers()
        # Equivalent to copy._keep_alive, without an extra dispatch per object.
        self._originals = self.memo.setdefault(id(self.memo), [])

    def copy(self, value, memo=None):
        """!
        @brief Copy an owned graph with a single memo preserving aliases and bindings.
        """
        cls = type(value)
        if self._primitive_identity and cls in _VALUES:
            return value
        memo = self.memo
        identity = id(value)
        if identity in memo:
            return memo[identity]
        if cls in _VALUES:
            return value
        if cls is dict:
            # Root-template recipes already avoid traversing constant entries.
            # They are never cached against mutable checkpoint dictionaries.
            plans = self.dictionary_plans
            dynamic = (plans.get(value, memo, primitive_identity=self._primitive_identity)
                       if plans is not None else None)
            if dynamic is not None:
                result = value.copy()
                memo[identity] = result
                for key, item in dynamic:
                    result[key] = self.copy(item)
            else:
                result = {}
                memo[identity] = result
                clone, atoms, lookup = self.copy, _VALUES, memo.get
                if self._primitive_identity:
                    for key, item in value.items():
                        result[key if type(key) in atoms else clone(key)] = item if type(item) in atoms else clone(item)
                else:
                    for key, item in value.items():
                        # Generic memo replacements still apply to primitives.
                        key = lookup(id(key), key) if type(key) in atoms else clone(key)
                        item = lookup(id(item), item) if type(item) in atoms else clone(item)
                        result[key] = item
        elif cls is list:
            result = []
            memo[identity] = result
            clone, atoms, lookup = self.copy, _VALUES, memo.get
            if self._primitive_identity:
                result.extend(item if type(item) in atoms else clone(item) for item in value)
            else:
                result.extend(lookup(id(item), item) if type(item) in atoms else clone(item) for item in value)
        elif cls is tuple:
            clone, atoms, lookup = self.copy, _VALUES, memo.get
            items = ([item if type(item) in atoms else clone(item) for item in value]
                     if self._primitive_identity else
                     [lookup(id(item), item) if type(item) in atoms else clone(item) for item in value])
            # A tuple may be revisited through a mutable member while copying.
            if identity in memo:
                return memo[identity]
            result = (value if all(old is new for old, new in zip(value, items))
                      else tuple(items))
        elif cls is frozenset:
            items = [self.copy(item) for item in value]
            if identity in memo:
                return memo[identity]
            result = (value if all(old is new for old, new in zip(value, items))
                      else frozenset(items))
        elif cls is set:
            result = set()
            memo[identity] = result
            result.update(self.copy(item) for item in value)
        elif cls is FunctionType and not (value.__closure__ or value.__defaults__ or value.__kwdefaults__):
            return value
        elif (cls is self._bitset_type
              and value.__dict__.keys() == {"_bits", "_max_elements", "_all_bits_mask"}
              and all(type(item) is int for item in value.__dict__.values())):
            result = cls(value)
        elif cls in (self._cow_index_type, self._frozen_bitset_type):
            # These native classes have more specific ownership adapters than
            # the general record path (notably COW fork and bitset capacity).
            return super().copy(value)
        elif self.component_copies and cls in self._native_dicts:
            # Install the shell before fields/members: zones can reach their
            # owning player and state, and callbacks may point back here.
            # Raw insertion must not run gameplay/cost-transaction write hooks.
            result = dict.__new__(cls)
            memo[identity] = result
            object.__setattr__(result, '__dict__', self.copy(value.__dict__))
            clone = self.copy
            dict.update(result, ((clone(key), clone(item)) for key, item in value.items()))
        elif self.component_copies and cls in self._native_frozen_maps:
            # These native wrappers store their contents in _dict. Copying
            # through pickle creates extra temporary dictionaries and cannot
            # install the wrapper before cyclic/runtime members are rebound.
            result = object.__new__(cls)
            memo[identity] = result
            object.__setattr__(result, '__dict__', self.copy(value.__dict__))
            result._hash = None  # Hash the rebound keys/values on demand.
        elif _plain_record(cls) and hasattr(value, "__dict__"):
            result = object.__new__(cls)
            memo[identity] = result
            if self.component_copies and cls in self._native_identities:
                # A back-edge can insert this shell in an index before the
                # owning state has finished copying. Install its stable hash
                # identity first, respecting even explicit key replacements.
                object.__setattr__(result, '_key', self.copy(value._key))
            attributes = self.copy(value.__dict__)
            if self.component_copies:
                # The memoized attribute mapping is the actual component
                # mapping, preserving aliases to it and avoiding a second dict.
                object.__setattr__(result, '__dict__', attributes)
            else:
                result.__dict__.update(attributes)
            if _live_mapping(cls):
                result._proxy = MappingProxyType(result._data)
                memo[id(value._proxy)] = result._proxy
        else:
            return super().copy(value)
        memo[identity] = result
        self._originals.append(value)
        return result
