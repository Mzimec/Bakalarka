"""Private graph copying for native search scenarios, never a policy input.

Unlike deepcopy, this copies captured callback state and read-only mappings.
It does not copy modules/globals or sandbox extension code: callbacks have the
same no-I/O, no mutable-global-runtime contract as replay scenario factories.
"""
import copy
import copyreg
from functools import lru_cache
from types import FunctionType, MethodType, MappingProxyType, CellType, BuiltinMethodType, ModuleType

# Most graph edges are primitive metadata. Avoid memo/import/dispatch overhead
# for the exact immutable types whose standard deepcopy is the identity.
_ATOMIC = frozenset(cls for cls, copier in copy._deepcopy_dispatch.items()
                    if copier is copy._deepcopy_atomic)
_VALUE_ATOMS = _ATOMIC - {FunctionType, BuiltinMethodType}


class DictionaryCopyPlans:
    """!
    @brief Bounded recipes for dictionaries in one never-executed root template.

    Collect only during its first copy. Reduction protocols can manufacture
    temporary dictionaries; retaining originals prevents ID reuse, and sealing
    prevents accumulating such temporaries on subsequent restores.
    """
    def __init__(self, immutable_values=()):
        self.constants = frozenset(dict(immutable_values))
        self.plans = {}
        self.collecting = True

    def get(self, value, memo, *, primitive_identity=False):
        """!
        @brief Return a guarded plan for copying only dynamic dictionary entries.
        """
        identity = id(value)
        entry = self.plans.get(identity)
        if entry is not None:
            return entry[1]
        if not self.collecting:
            return None
        return self._compile(value, memo, primitive_identity)

    def _compile(self, value, memo, primitive_identity=False):
        # Keep closure creation out of get(): even an early-return invocation
        # would allocate closure cells for a nested function's captured locals.
        identity = id(value)
        def constant(item):
            if primitive_identity and type(item) in _VALUE_ATOMS:
                return True
            return (memo.get(id(item), item) is item
                    and (id(item) in self.constants
                         or (type(item) in _ATOMIC
                             and type(item) not in (FunctionType, BuiltinMethodType))))

        # Preserve insertion order and arbitrary runtime-key rebinding by
        # leaving dictionaries with nonconstant keys to the general copier.
        if not all(constant(key) for key in value):
            self.plans[identity] = (value, None)
            return None
        dynamic = tuple((key, item) for key, item in value.items() if not constant(item))
        self.plans[identity] = (value, dynamic)
        return dynamic


@lru_cache(maxsize=512)
def _plain_record(cls):
    """!
    @brief Native dictionary-backed records with no custom serialization contract.
    """
    return (cls.__module__.startswith(("game.", "helper."))
            and cls.__new__ is object.__new__
            and cls.__reduce_ex__ is object.__reduce_ex__
            and cls.__reduce__ is object.__reduce__
            and cls.__getstate__ is object.__getstate__
            and not hasattr(cls, "__getnewargs__") and not hasattr(cls, "__getnewargs_ex__")
            and not hasattr(cls, "__setstate__") and not copyreg._slotnames(cls))


class UnsupportedSnapshot(ValueError):
    """!
    @brief The current position needs an adapter not supplied by this backend.
    """


class InaccessibleHistorySource(UnsupportedSnapshot):
    """!
    @brief A known impossible source edge, detected before copying the graph.
    """


def reject_unknown_history_sources(state, visible_cards, observer):
    """!
    @brief Fast preflight for edges the current copier cannot reconstruct.

    The bridge reconstructs only observed own-library references. An exact
    native archived event pointing to a currently hidden opponent card must
    therefore fail the same visibility guard later. Do not inspect its printed
    identity, incarnation, private payload, or other player's projection.
    Own/private-inspection sources and extension events retain full dispatch.
    """
    from game.game_state.card import Card
    from game.game_actions.resolution.event_bus import GameEvent
    allowed = {id(card) for card in visible_cards}
    for event in getattr(state, "turn_events", ()):
        if (type(event) is GameEvent and isinstance(event.source, Card)
                and id(event.source) not in allowed and event.source.owner is not observer):
            raise InaccessibleHistorySource("A public rule retains an inaccessible card reference")


def snapshot_failure_trace(error):
    """!
    @brief Failure-only structural breadcrumbs, never object reprs/card identities.

    Walk the copier traceback rather than instrumenting every successful copy.
    Only reviewed engine field names may be exposed; other mapping keys are masked.
    """
    if type(error) is InaccessibleHistorySource:
        return ["State", "turn_events", "GameEvent", "source", "Card"]
    fields = {"turn_events", "payload", "source", "target", "top", "bottom",
              "players", "hand", "deck", "stack", "combat", "state", "context",
              "pending_triggers", "combat_declaration_boundary", "abilities",
              "effects", "definition", "controller", "owner", "_game_state"}
    path = []
    frame = error.__traceback__
    while frame is not None:
        name, local = frame.tb_frame.f_code.co_name, frame.tb_frame.f_locals
        if name == "_deepcopy_dict":
            key = local.get("key")
            path.append(key if type(key) is str and key in fields else "<mapping-entry>")
        elif name in {"_deepcopy_list", "_deepcopy_tuple"}:
            path.append("<sequence-item>")
        elif name == "copy" and "value" in local:
            path.append(type(local["value"]).__name__)
        frame = frame.tb_next
    return path


class RuntimeClone:
    """!
    @brief Copy or sanitize runtime graphs while preserving reference aliases.
    """
    def __init__(self, replacements=(), *, allowed_cards=None, known_card_factory=None,
                 dictionary_plans=None):
        from game.game_state.card import Card
        from helper.dict_helper import MutableCounterMapping, MutableSetMapping
        from helper.query_system.object_register import BitSet
        from helper.query_system.cow_index import CowIndexProvider, FrozenBitSet
        self._card_type = Card
        self._bitset_type = BitSet
        self._cow_index_type = CowIndexProvider
        self._frozen_bitset_type = FrozenBitSet
        self._view_types = (MutableCounterMapping, MutableSetMapping)
        self.memo = dict(replacements)
        self.allowed_cards = allowed_cards
        self.known_card_factory = known_card_factory
        self.reconstructed_cards = {}
        self.dictionary_plans = dictionary_plans

    def copy(self, value, memo=None):
        """!
        @brief Copy an owned graph with a single memo preserving aliases and bindings.
        """
        memo = self.memo
        identity = id(value)
        if identity in memo:
            return memo[identity]
        cls = type(value)
        if cls in _ATOMIC and cls not in (FunctionType, BuiltinMethodType):
            return value
        if self.allowed_cards is not None and isinstance(value, self._card_type) and identity not in self.allowed_cards:
            replacement = self.known_card_factory(value) if self.known_card_factory is not None else None
            if replacement is not None:
                self.allowed_cards.add(id(replacement))
                result = self.copy(replacement)
                memo[identity] = result
                self.reconstructed_cards[result.key] = result
                copy._keep_alive(value, memo)
                return result
            raise UnsupportedSnapshot("A public rule retains an inaccessible card reference")
        if (cls is self._bitset_type
                and value.__dict__.keys() == {"_bits", "_max_elements", "_all_bits_mask"}
                and all(type(v) is int for v in value.__dict__.values())):
            # Native query bitsets own only three integers. Their copy
            # constructor preserves capacity as well as membership.
            result = cls(value)
        elif cls is MappingProxyType:
            data = {}
            result = MappingProxyType(data)
            memo[identity] = result
            data.update((self.copy(k), self.copy(v)) for k, v in value.items())
        elif cls is FunctionType:
            if not value.__closure__ and not value.__defaults__ and not value.__kwdefaults__:
                return value
            cells = tuple(CellType() for _ in (value.__closure__ or ()))
            result = FunctionType(value.__code__, value.__globals__, value.__name__, closure=cells or None)
            memo[identity] = result
            for old, new in zip(value.__closure__ or (), cells):
                try:
                    contents = old.cell_contents
                except ValueError:
                    continue
                new.cell_contents = self.copy(contents)
            result.__defaults__ = self.copy(value.__defaults__)
            result.__kwdefaults__ = self.copy(value.__kwdefaults__)
            result.__dict__.update(self.copy(value.__dict__))
        elif cls is MethodType:
            result = MethodType(self.copy(value.__func__), self.copy(value.__self__))
        elif cls is BuiltinMethodType and value.__self__ is not None and not isinstance(value.__self__, ModuleType):
            result = getattr(self.copy(value.__self__), value.__name__)
        elif cls is dict and self.dictionary_plans is not None:
            dynamic = self.dictionary_plans.get(value, memo)
            if dynamic is None:
                result = copy._deepcopy_dict(value, memo, deepcopy=self.copy)
            else:
                result = value.copy()
                memo[identity] = result
                for key, item in dynamic:
                    result[key] = self.copy(item)
        elif cls in (list, dict, tuple):
            result = copy._deepcopy_dispatch[cls](value, memo, deepcopy=self.copy)
        elif cls is frozenset:
            # Index snapshots contain thousands of small immutable sets. Avoid
            # the pickle reduction protocol, but still rebind runtime members
            # such as players/cards through the same ownership memo.
            items = [self.copy(item) for item in value]
            result = memo[identity] if identity in memo else frozenset(items)
        elif cls is set:
            result = set()
            memo[identity] = result
            result.update(self.copy(item) for item in value)
        elif cls is self._frozen_bitset_type:
            return value
        elif cls is self._cow_index_type:
            result = value.fork(self)
        elif cls in copy._deepcopy_dispatch:
            result = copy._deepcopy_dispatch[cls](value, memo)
        elif isinstance(value, type):
            return value
        else:
            # Use the standard reduction protocol, but route every nested value
            # through this copier (including callbacks in frozen definitions).
            if _plain_record(cls) and hasattr(value, "__dict__"):
                result = object.__new__(cls)
                memo[identity] = result
                result.__dict__.update(self.copy(value.__dict__))
            else:
                reduced = value.__reduce_ex__(4)
                if isinstance(reduced, str):
                    return value
                result = copy._reconstruct(value, memo, *reduced, deepcopy=self.copy)
            # These proxies are live views of mutable backing dictionaries,
            # not independent frozen snapshots. Copying both independently
            # makes subsequent mana additions invisible to mapping readers.
            if isinstance(value, self._view_types):
                result._proxy = MappingProxyType(result._data)
                memo[id(value._proxy)] = result._proxy
        memo[identity] = result
        # Keep originals alive until the copy ends; callbacks/reducers may
        # allocate temporaries whose ids must not be recycled inside this memo.
        copy._keep_alive(value, memo)
        return result
