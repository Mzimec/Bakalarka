"""Skip an SBA only while its indexed candidate set is provably empty.

Nonempty sets are never evidence that a rule is unchanged: damage, loyalty,
attachment predicates and custom getters can change without changing membership.
"""
from __future__ import annotations
from weakref import ref

from helper.query_system.query import QueryContext


class SBACandidateFilter:
    """!
    @brief Cache only provably empty indexed SBA candidate selections.
    """
    def __init__(self, query: object, indexes: object, *, players: bool = False) -> None:
        """!
        @brief Configure the query and indexes used by this candidate filter.
        @param query Query selecting possible SBA candidates.
        @param indexes Index keys that invalidate cached emptiness.
        @param players Whether to query players instead of cards.
        """
        self.query = query
        self.indexes = tuple(indexes)
        self.players = players
        self._register = None
        self._empty_token = None

    def empty(self, state: object) -> bool:
        """!
        @brief Return whether the current indexed selection is provably empty.
        @param state Current game state.
        @return True when no candidates exist.
        """
        candidates = self.select(state)
        return candidates is not None and not candidates

    def select(self, state: object) -> tuple[object, ...] | None:
        """!
        @brief Synchronize derived values, then reuse only an empty query result.
        """
        from ...game_state.state import State
        from ...game_state.registers.card_register import CardRegister
        from ...game_state.registers.player_register import PlayerRegister

        if type(state) is not State:
            return None
        register_name, method = ("player_register", "query_players") if self.players else ("card_register", "query_cards")
        register = getattr(state, register_name)
        expected = PlayerRegister if self.players else CardRegister
        if (type(register) is not expected or method in vars(state)
                or "query" in vars(register) or "synchronise" in vars(register)):
            return None
        register.synchronise()
        # Never treat committed intermediate-layer/recursive data as settled.
        if register._synchronising or state._refreshing_effects or state._layer_ceiling is not None:
            return None
        token = register.membership_token(self.indexes)
        if (self._register is not None and self._register() is register
                and self._empty_token == token):
            return ()
        self._empty_token = None
        matches = self.query.eval(QueryContext(register.storage, register.idx_provider))
        if matches:
            # Materialize once in the same stable order as IndexedRegister.query.
            # The resolver passes this fresh selection directly to collect().
            return tuple(register.storage.get(identity) for identity in matches)
        self._register = ref(register)
        self._empty_token = token
        return ()
