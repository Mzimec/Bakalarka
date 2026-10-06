"""Incremental synchronization of runtime objects with shared query indexes."""

from __future__ import annotations

from helper.query_system.object_register import (
    InMemoryObjectRegister,
    IndexUpdate,
    RegisterUpdateContext,
)


class IndexedRegister(InMemoryObjectRegister):
    """!
    @brief Runtime-object register with deferred incremental index updates.

    Objects are registered under stable entity keys while their derived index
    memberships are cached in immutable snapshots. Runtime mutations only mark
    an object dirty; actual index changes are applied lazily by `synchronise()`.
    """

    def __init__(self, state, index_keys, ordered_keys=()):
        """!
        @brief Initialize the indexed register.

        @param state Owning game state.
        @param index_keys Index groups maintained by the base register.
        @param ordered_keys Index groups requiring ordered storage.
        """
        index_keys = tuple(index_keys)
        super().__init__(index_keys, ordered_keys)
        self.state = state
        self._by_key = {}
        self._snapshots = {}
        self._dirty = {}
        self._dirty_indexes = {}
        # Opaque tokens cannot collide after a checkpoint rollback and mutation.
        self.change_token = object()
        # Membership revisions change only when committed index buckets change,
        # independently of broad dirty notifications and unrelated index keys.
        self._membership_tokens = dict.fromkeys(index_keys, object())
        self._synchronising = False

    def membership_token(self, indexes):
        """Revision of selected index memberships; synchronize before reading.

        Opaque identities remain distinct across rollback followed by new writes.
        This describes query membership, not arbitrary runtime object values.
        """
        return tuple(self._membership_tokens[index] for index in indexes)

    def _touch_memberships(self, indexes):
        token = object()
        for index in indexes:
            self._membership_tokens[index] = token

    def update(self, first, ctx):
        super().update(first, ctx)
        self._touch_memberships(ctx.data)

    def index_values(self, obj):
        """!
        @brief Return the current derived index memberships for an object.

        Subclasses must return a mapping from every supported index key to its
        immutable set of current values.

        @param obj Runtime object being indexed.
        @return Mapping of index keys to current memberships.
        """
        raise NotImplementedError

    def changed_index_values(self, obj, indexes):
        """Read affected memberships; subclasses may return a partial mapping."""
        return self.index_values(obj)

    def get_by_key(self, key):
        """!
        @brief Return the registered object with the supplied entity key.

        @param key Stable entity key.
        @return Registered object, or `None` if absent.
        """
        return self._by_key.get(key)

    def validate_new(self, obj):
        """!
        @brief Validate that an object may be registered here.

        Re-registering the same object is allowed as an idempotent operation,
        while another object with the same key or an already registered runtime
        object is rejected.

        @param obj Runtime object to validate.
        @throws ValueError If its key is already occupied by another object or
            the object already belongs to another registry.
        """
        if obj.key in self._by_key:
            if self._by_key[obj.key] is obj:
                return
            raise ValueError(f"Duplicate entity key: {obj.key}")

        if obj.runtime_id is not None:
            raise ValueError(
                f"Entity '{obj.key}' is already registered in another registry."
            )

    def register(self, obj):
        """!
        @brief Add an object and initialize all of its index memberships.

        @param obj Runtime object to register.
        """
        self.validate_new(obj)

        if self._by_key.get(obj.key) is obj:
            return

        values = self.index_values(obj)

        self.add(
            obj,
            RegisterUpdateContext(
                {
                    key: IndexUpdate((), value)
                    for key, value in values.items()
                }
            ),
        )

        self._by_key[obj.key] = obj
        self._snapshots[obj.key] = values
        self._touch_memberships(values)
        self.change_token = object()

        # Register membership can affect continuous-effect queries.
        self.state._effects_dirty = True

    def mark_changed(self, obj, indexes=None):
        """!
        @brief Mark a registered object for deferred index synchronization.

        Multiple mutations before synchronization collapse into one dirty entry;
        the eventual update compares the final object state with its last stored
        snapshot.

        @param obj Runtime object whose indexed characteristics changed.
        """
        if self._by_key.get(obj.key) is obj:
            # A full notification dominates every partial notification until sync.
            previous = self._dirty_indexes.get(obj.key, frozenset())
            self._dirty_indexes[obj.key] = (
                None if indexes is None or previous is None else previous | indexes
            )
            self._dirty[obj.key] = obj
            self.change_token = object()
            self.state._effects_dirty = True

    def synchronise(self):
        """!
        @brief Synchronize pending object changes with the derived indexes.

        Continuous effects are refreshed first because they may affect values
        returned by `index_values()`. Each dirty object is then diffed against
        its previous immutable snapshot and only changed memberships are sent to
        the underlying index provider.
        """
        # Formula modifiers may query other cards while their own snapshot is
        # being computed. Read committed memberships in that nested query;
        # the layer pipeline synchronizes again after applying each layer.
        if self._synchronising:
            return
        self.state.refresh_continuous_effects()
        self._synchronising = True
        try:

            # Snapshots preserve the previous memberships independently of the
            # mutable runtime object, so obsolete index values can still be removed
            # after that object has already changed.
            while self._dirty:
                key = next(iter(self._dirty))
                obj = self._dirty[key]

                current = self.changed_index_values(obj, self._dirty_indexes.get(key))
                previous = self._snapshots[key]

                changes = {
                    index: IndexUpdate(
                        previous[index] - value,
                        value - previous[index],
                    )
                    for index, value in current.items()
                    if value != previous[index]
                }

                if changes:
                    self.update(
                        obj,
                        RegisterUpdateContext(changes),
                    )

                self._snapshots[key] = current if len(current) == len(previous) else previous | current
                self._dirty.pop(key)
                self._dirty_indexes.pop(key, None)
        finally:
            self._synchronising = False

    def unregister(self, obj):
        """!
        @brief Remove an object and all memberships from this register.

        Removal uses the last synchronized snapshot instead of recomputing from
        the current object, ensuring the exact memberships known by the indexes
        are removed.

        @param obj Registered runtime object to remove.
        @throws ValueError If the exact object is not registered here.
        """
        if self._by_key.get(obj.key) is not obj:
            raise ValueError(
                f"Entity '{obj.key}' is not in this registry."
            )

        previous = self._snapshots[obj.key]

        self.remove(
            obj,
            RegisterUpdateContext(
                {
                    index: IndexUpdate(value, ())
                    for index, value in previous.items()
                }
            ),
        )

        self._snapshots.pop(obj.key)
        self._by_key.pop(obj.key)
        self._dirty.pop(obj.key, None)
        self._dirty_indexes.pop(obj.key, None)
        self._touch_memberships(previous)
        self.change_token = object()
        self.state._effects_dirty = True

    def query(self, query):
        """!
        @brief Evaluate a query against synchronized indexes.

        The result is materialized before returning so callers may safely mutate
        objects and thereby update the register while iterating the query result.

        @param query Query to evaluate.
        @return Iterator over a stable snapshot of matching objects.
        """
        self.synchronise()

        # A stable result is safe to iterate while operations mutate objects and
        # mark or update the underlying index during that same iteration.
        return iter(
            tuple(
                super().query(query)
            )
        )

    def contains(self, query, obj):
        """!
        @brief Test whether one registered object satisfies a query.

        @param query Query to evaluate.
        @param obj Runtime object to test.
        @return `True` if the exact registered object belongs to the query result.
        """
        from helper.query_system.query import QueryContext

        self.synchronise()

        if self._by_key.get(obj.key) is not obj:
            return False

        return query.eval(
            QueryContext(
                self.storage,
                self.idx_provider,
            )
        ).contains(obj.runtime_id)

    def clear(self):
        """!
        @brief Remove all objects, snapshots and pending dirty state.
        """
        super().clear()
        self._by_key.clear()
        self._snapshots.clear()
        self._dirty.clear()
        self._dirty_indexes.clear()
        self._touch_memberships(tuple(self._membership_tokens))
        self.change_token = object()
