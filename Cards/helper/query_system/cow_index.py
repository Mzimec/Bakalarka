"""Experimental copy-on-write index pages for owned simulation branches.

Only value keys are shared. Runtime keys (players, cards, callbacks, custom
objects) are rebound by the world's graph copier. This is not a COW game state.
"""
from enum import Enum
from types import MappingProxyType

from .object_register import BitSet, IndexUpdate, SimpleObjectIndexProvider


class FrozenBitSet(BitSet):
    """Read-only membership value; query arithmetic returns ordinary BitSets."""

    def __init__(self, source):
        for name in ("_bits", "_max_elements", "_all_bits_mask"):
            object.__setattr__(self, name, getattr(source, name))

    def __setattr__(self, name, value):
        raise TypeError("Index memberships must be changed through their provider")

    def __delattr__(self, name):
        raise TypeError("Index memberships must be changed through their provider")


def _value_key(value):
    if type(value) in (str, int, float, bool, bytes, type(None)) or isinstance(value, Enum):
        return True
    if type(value) in (tuple, frozenset):
        return all(_value_key(item) for item in value)
    return False


class CowIndexProvider(SimpleObjectIndexProvider):
    """Share immutable groups, detach a group on its first write after a fork.

    Buckets are always immutable; changing one replaces only that bucket.
    Forking never changes the parent, and retains no chain of ancestor overlays.
    Private backing dictionaries must not be modified outside this class.
    """

    def __init__(self, index_keys, ordered_keys=()):
        super().__init__(index_keys, ordered_keys)
        self._shareable = set(self._data)

    @classmethod
    def capture(cls, source):
        if type(source) is not SimpleObjectIndexProvider:
            raise TypeError("Only the native index provider can be captured")
        result = cls((), source._ordered_keys)
        result._data = {
            key: MappingProxyType({value: FrozenBitSet(bits) for value, bits in group.items()})
            for key, group in source.data.items()
        }
        result._shareable = {key for key, group in result._data.items()
                             if all(_value_key(value) for value in group)}
        return result

    @property
    def data(self):
        return MappingProxyType({key: self.get_index_group(key) for key in self._data})

    def get_index_group(self, key):
        group = super().get_index_group(key)
        return MappingProxyType(group) if type(group) is dict else group

    def fork(self, clone):
        """Rebind runtime keys with the same memo as all cards and players."""
        result = type(self)(())
        clone.memo[id(self)] = result
        result._ordered_keys = clone.copy(self._ordered_keys)
        result._shareable = set()
        for key, group in self._data.items():
            copied_key = clone.copy(key)
            if key in self._shareable:
                # A changed parent page still owns a dict. Freeze a COPY so
                # subsequent parent writes cannot alter the child's snapshot.
                result._data[copied_key] = (MappingProxyType(group.copy())
                                            if type(group) is dict else group)
                result._shareable.add(copied_key)
            else:
                result._data[copied_key] = MappingProxyType({
                    clone.copy(value): bits for value, bits in group.items()
                })
        return result

    def _process_register_update(self, idx, ctx, op):
        for key, update in ctx.data.items():
            group = self._data[key]
            additions = tuple(update.add)
            # Classify BEFORE writing: even a partly failed update must never
            # leave a runtime key in a group marked safe to share. Inspect only
            # new keys, not every existing bucket on every membership change.
            if key in self._shareable and not all(_value_key(value) for value in additions):
                self._shareable.remove(key)
            if type(group) is not dict:
                group = self._data[key] = dict(group)
            op(idx, IndexUpdate(update.remove, additions), group)

    def _add_on_idx(self, idx, upd, group):
        for value in upd.add:
            bits = BitSet(group.get(value, 0))
            bits.add(idx)
            group[value] = FrozenBitSet(bits)

    def _remove_on_idx(self, idx, upd, group):
        for value in upd.remove:
            previous = group.get(value)
            if previous is None or not previous.contains(idx):
                raise RuntimeError(f"Missing object {idx} in index bucket")
            bits = BitSet(previous)
            bits.remove(idx)
            if bits:
                group[value] = FrozenBitSet(bits)
            else:
                del group[value]

    def clear(self):
        self._data = {key: {} for key in self._data}
        self._shareable = set(self._data)
