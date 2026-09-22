"""Rollback boundary for costs, preserving existing runtime object identities.

This is an in-memory checkpoint, not a simulation clone or persistent save file.
Definitions, controller state, callback closures and external I/O are outside the
boundary. Custom costs must use engine runtime state and pure reserve_cost hooks.
"""

from collections import deque
from random import Random


class CostPaymentError(ValueError):
    """!
    @brief Error raised when a cost cannot be paid successfully.
    """

    pass


class RuntimeCheckpoint:
    """!
    @brief Snapshot mutable runtime state for in-place rollback.

    Containers and engine runtime objects are restored in place so that
    indexes, attachments, selected targets, and other references keep
    pointing to the same object identities after rollback.

    Card definitions and other immutable/external structures are not
    copied. Frozen runtime wrappers are skipped except for explicitly
    mutable state stored in their `state` attribute.
    """

    def __init__(self, state):
        """!
        @brief Create a checkpoint rooted at the supplied game state.

        @param state Root runtime state whose mutable object graph should
               be tracked.
        """
        self._seen = set()
        self._restore = []
        self.watch(state, root=True)

    def watch(self, value, *, root=False):
        """!
        @brief Add a mutable value and its relevant descendants to the checkpoint.

        Each object identity is visited at most once. Mutable containers
        and engine runtime objects receive shallow snapshots, while immutable
        containers are traversed only to discover mutable children.

        @param value Runtime value to inspect and potentially snapshot.
        @param root Force inspection of the supplied object even if its module
               is outside the normal engine namespaces.
        """
        identity = id(value)

        # Avoid cycles and duplicate snapshots of shared runtime objects.
        if identity in self._seen:
            return

        self._seen.add(identity)

        # Random must preserve its internal generator state so rollback also
        # restores deterministic future draws.
        if isinstance(value, Random):
            self._restore.append(("rng", value, value.getstate()))
            return

        if isinstance(value, dict):
            saved = dict(value)
            self._restore.append(("dict", value, saved))
            children = saved.values()

        elif isinstance(value, list):
            saved = list(value)
            self._restore.append(("list", value, saved))
            children = saved

        elif isinstance(value, set):
            saved = set(value)
            self._restore.append(("set", value, saved))
            children = saved

        elif isinstance(value, deque):
            saved = tuple(value)
            self._restore.append(("deque", value, saved))
            children = saved

        elif isinstance(value, (tuple, frozenset)):
            # Immutable containers do not need restoration, but may contain
            # mutable runtime objects that must still be visited.
            children = value

        elif root or type(value).__module__.startswith(("game.", "helper.")):
            attributes = getattr(value, "__dict__", None)
            if attributes is None:
                return

            # Some frozen dataclass wrappers still contain an explicitly
            # mutable `.state`; preserve that state without replacing the
            # wrapper identity itself.
            if getattr(
                type(value).__dict__.get("__dataclass_params__"),
                "frozen",
                False,
            ):
                if "state" in attributes:
                    self.watch(attributes["state"])
                return

            saved = dict(attributes)
            self._restore.append(("object", value, saved))

            # These fields point to definitions/controllers or derived
            # structures outside the rollback boundary.
            children = (
                child
                for key, child in saved.items()
                if key
                not in {
                    "_controller",
                    "definition",
                    "_definition",
                    "_stats",
                    "_modifier_sources",
                }
            )

        else:
            return

        for child in children:
            self.watch(child)

    def rollback(self):
        """!
        @brief Restore every tracked runtime value to its checkpointed state.

        Restoration runs in reverse discovery order so nested mutations are
        undone before their containing objects.
        """
        for kind, value, saved in reversed(self._restore):
            if kind == "object":
                value.__dict__.clear()
                value.__dict__.update(saved)

            elif kind == "dict":
                dict.clear(value)
                dict.update(value, saved)

            elif kind == "list":
                value[:] = saved

            elif kind == "set":
                value.clear()
                value.update(saved)

            elif kind == "deque":
                value.clear()
                value.extend(saved)

            else:
                value.setstate(saved)


class CostTransaction:
    """!
    @brief Transaction boundary for reversible cost payment.

    Captures the mutable runtime state before costs are paid, buffers events
    until the transaction commits, and restores the checkpoint if payment
    fails.
    """

    def __init__(self, state):
        """!
        @brief Start a cost transaction for the supplied game state.

        @param state Current game state to checkpoint.
        """
        self.state = state

        # Remember pre-existing card identities so cards created during a
        # failed custom cost can be detached after rollback.
        register = getattr(state, "card_register", None)
        self.registration_cursor = register.registration_cursor if register is not None else None
        self.original_cards = ({id(card) for card in getattr(state, "get_cards", lambda: ())()}
                               if register is None else None)

        self.checkpoint = RuntimeCheckpoint(state)
        self.events = []

    def watch_operations(self, operations):
        """!
        @brief Extend the checkpoint with mutable state owned by cost operations.

        @param operations Operations whose runtime fields may be mutated while
               reserving or paying a cost.
        """
        for operation in operations:
            self.checkpoint.watch(operation, root=True)

    def rollback(self):
        """!
        @brief Restore the checkpoint and discard all buffered transaction events.

        Runtime identities created during the failed transaction are detached
        after restoration so they no longer appear associated with the game
        state.
        """
        # Find cards created after the checkpoint before restoring the state's
        # original containers.
        created = (
            self.state.card_register.introduced_after(self.registration_cursor)
            if self.registration_cursor is not None else [
                card for card in getattr(self.state, "get_cards", lambda: ())()
                if id(card) not in self.original_cards
            ]
        )

        self.checkpoint.rollback()

        # Objects created during the failed cost are no longer owned by the
        # restored state, so detach their runtime identity/state association.
        for card in created:
            card.runtime_id = None
            card._game_state = None

        self.events.clear()
