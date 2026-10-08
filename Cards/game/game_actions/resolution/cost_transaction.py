"""Rollback boundary for costs, preserving existing runtime object identities.

This is an in-memory checkpoint, not a simulation clone or persistent save file.
Definitions, controller state, callback closures and external I/O are outside the
boundary. Custom costs must use engine runtime state and pure reserve_cost hooks.
"""

from __future__ import annotations
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

    def __init__(self, state: object, *, card_scope: object | None = None, shallow_roots: object | None = None) -> None:
        """!
        @brief Create a checkpoint rooted at the supplied game state.
        @param state Root runtime state whose mutable object graph should
               be tracked.
        @param card_scope Proven card write set; None retains the full graph.
        @param shallow_roots Proven stack-only writes, without graph traversal.
               Supplied only by the empty-trigger-cost planner.
        """
        self._seen = set()
        self._restore = []
        self.card_scope = card_scope
        self.skipped_cards = 0
        self.mode = (
            "stack" if shallow_roots is not None
            else "full" if card_scope is None else "scoped"
        )
        if shallow_roots is not None:
            for value in shallow_roots:
                self._seen.add(id(value))
                if type(value) is list:
                    self._restore.append(("list", value, value.copy()))
                else:
                    self._restore.append(("object", value, vars(value).copy()))
            return
        if card_scope is not None:
            # Native register synchronization replaces rows; it never mutates
            # the old membership mappings/frozensets in place. Preserve the
            # original row identities without traversing every card's values.
            # Unknown cost plans keep the recursive full-checkpoint behavior.
            for register in (state.card_register, state.player_register):
                rows = register._snapshots
                self._seen.add(id(rows))
                self._restore.append(("dict", rows, rows.copy()))
        self.watch(state, root=True)

    def watch(self, value: object, *, root: bool = False) -> None:
        """!
        @brief Add a mutable value and its relevant descendants to the checkpoint.
        Each object identity is visited at most once. Mutable containers
        and engine runtime objects receive shallow snapshots, while immutable
        containers are traversed only to discover mutable children.
        @param value Runtime value to inspect and potentially snapshot.
        @param root Force inspection of the supplied object even if its module
               is outside the normal engine namespaces.
        """
        # Classify once per type in this traversal. In particular, scalar index
        # values need no snapshot or repeated isinstance checks.
        # Exact types only: subclasses may own mutable runtime attributes.
        kinds = {int: "ignore", float: "ignore", bool: "ignore", str: "ignore",
                 bytes: "ignore", complex: "ignore", type(None): "ignore",
                 object: "ignore", dict: "dict", list: "list", set: "set",
                 deque: "deque", tuple: "tuple", frozenset: "frozenset"}
        pending = [value]
        seen = self._seen
        restore = self._restore
        from ...game_state.card import Card
        card_scope = self.card_scope
        excluded = {"_controller", "definition", "_definition", "_stats",
                    "_modifier_sources"}
        while pending:
            value = pending.pop()
            force_root, root = root, False
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            cls = type(value)
            kind = kinds.get(cls)
            if kind is None:
                if issubclass(cls, Random):
                    kind = "rng"
                elif issubclass(cls, dict):
                    kind = "dict"
                elif issubclass(cls, list):
                    kind = "list"
                elif issubclass(cls, set):
                    kind = "set"
                elif issubclass(cls, deque):
                    kind = "deque"
                elif issubclass(cls, tuple):
                    kind = "tuple"
                elif issubclass(cls, frozenset):
                    kind = "frozenset"
                else:
                    kind = "object" if cls.__module__.startswith(("game.", "helper.")) else "ignore"
                kinds[cls] = kind
            if force_root and kind == "ignore":
                kind = "object"
            if kind == "ignore":
                continue

            if kind == "tuple" or kind == "frozenset":
                pending.extend(reversed(tuple(value)))
            elif kind == "object":
                if card_scope is not None:
                    if type(value) is Card and value not in card_scope:
                        self.skipped_cards += 1
                        continue
                attributes = getattr(value, "__dict__", None)
                if attributes is None:
                    continue
                if getattr(cls.__dict__.get("__dataclass_params__"), "frozen", False):
                    if "state" in attributes:
                        pending.append(attributes["state"])
                    continue
                saved = attributes.copy()
                restore.append(("object", value, saved))
                # Reverse the work stack to retain the previous depth-first
                # discovery order and corresponding reverse restoration order.
                pending.extend([child for key, child in reversed(saved.items())
                                if key not in excluded])
            elif kind == "dict":
                saved = dict(value)
                restore.append((kind, value, saved))
                pending.extend(reversed(saved.values()))
            elif kind == "list":
                saved = list(value)
                restore.append((kind, value, saved))
                pending.extend(reversed(saved))
            elif kind == "set":
                saved = set(value)
                restore.append((kind, value, saved))
                pending.extend(reversed(tuple(saved)))
            elif kind == "deque":
                saved = tuple(value)
                restore.append((kind, value, saved))
                pending.extend(reversed(saved))
            else:
                restore.append(("rng", value, value.getstate()))

    def rollback(self) -> None:
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

    def __init__(self, state: object, *, resolutions: object | None = None, action: object | None = None) -> None:
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

        from .cost_scope import card_write_scope, stack_only_cost
        shallow = (
            stack_only_cost(state, action, resolutions)
            if action is not None and resolutions is not None else None
        )
        scope = (
            card_write_scope(state, resolutions)
            if shallow is None and resolutions is not None else None
        )
        self.checkpoint = RuntimeCheckpoint(state, card_scope=scope, shallow_roots=shallow)
        self.events = []

    def watch_operations(self, operations: object) -> None:
        """!
        @brief Extend the checkpoint with mutable state owned by cost operations.
        @param operations Operations whose runtime fields may be mutated while
               reserving or paying a cost.
        """
        for operation in operations:
            self.checkpoint.watch(operation, root=True)

    def rollback(self) -> None:
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
