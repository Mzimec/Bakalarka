"""Skip an SBA only while its indexed candidate set is provably empty.

Nonempty sets are never evidence that a rule is unchanged: damage, loyalty,
attachment predicates and custom getters can change without changing membership.
"""
from weakref import ref

from helper.query_system.query import QueryContext


class SBACandidateFilter:
    def __init__(self, query, indexes, *, players=False):
        self.query = query
        self.indexes = tuple(indexes)
        self.players = players
        self._register = None
        self._empty_token = None

    def empty(self, state):
        """Synchronize derived values, then reuse only an empty query result."""
        from ...game_state.state import State
        from ...game_state.registers.card_register import CardRegister
        from ...game_state.registers.player_register import PlayerRegister

        if type(state) is not State:
            return False
        register_name, method = ("player_register", "query_players") if self.players else ("card_register", "query_cards")
        register = getattr(state, register_name)
        expected = PlayerRegister if self.players else CardRegister
        if (type(register) is not expected or method in vars(state)
                or "query" in vars(register) or "synchronise" in vars(register)):
            return False
        register.synchronise()
        # Never treat committed intermediate-layer/recursive data as settled.
        if register._synchronising or state._refreshing_effects or state._layer_ceiling is not None:
            return False
        token = register.membership_token(self.indexes)
        if (self._register is not None and self._register() is register
                and self._empty_token == token):
            return True
        self._empty_token = None
        if self.query.eval(QueryContext(register.storage, register.idx_provider)):
            return False
        self._register = ref(register)
        self._empty_token = token
        return True
