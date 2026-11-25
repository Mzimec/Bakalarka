"""Reuse pure modifier pipelines during a single synchronous index projection."""
from contextlib import contextmanager
from contextvars import ContextVar


_CURRENT = ContextVar("characteristic_scope", default=None)
current_scope = _CURRENT.get


@contextmanager
def characteristic_scope(state, card):
    # Only the caller's proven standard card is eligible. Nested reads of other
    # objects/custom formulas continue through the ordinary evaluator.
    token = _CURRENT.set((state, card, card.stats, state.card_register.change_token,
                          state.player_register.change_token, state._layer_ceiling,
                          state.time_stamp, state._refreshing_effects, {}))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def pipeline_cache(state, card, scope=None):
    scope = scope if scope is not None else _CURRENT.get()
    if (scope is not None and scope[0] is state and scope[1] is card
            and scope[2] is card.stats and scope[3] is state.card_register.change_token
            and scope[4] is state.player_register.change_token and scope[5] == state._layer_ceiling
            and scope[6] == state.time_stamp and scope[7] == state._refreshing_effects):
        return scope[8]
    return None
