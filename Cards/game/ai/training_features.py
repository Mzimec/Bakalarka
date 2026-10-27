"""Versioned pooled baseline over the existing identity-free graph encoders.

Physical IDs, card names, action keys and outcome metadata never enter vectors.
Rows remain menu-aligned; reference indices are resolved into node features.
Pooling is deliberately lossy, and is separate from the underlying encoder v1.
"""
from math import copysign, log1p, isfinite, fsum

from game.ai.search_encoding import (
    SCHEMA_VERSION, CARD_FEATURES, PLAYER_FEATURES, GLOBAL_FEATURES,
    ACTION_FEATURES, EFFECT_FEATURES, ActionEncoder,
)

FEATURE_VERSION = 1
C, P, E = len(CARD_FEATURES), len(PLAYER_FEATURES), len(EFFECT_FEATURES)
NODE_SIZE = 2 + C + P  # absent/card/player flags plus separate type-specific rows
STATE_SIZE = len(GLOBAL_FEATURES) + 2*P + 2*(1+C) + 3*(1+2*NODE_SIZE) + E
ACTION_SIZE = len(ACTION_FEATURES) + NODE_SIZE + 2*E + 3*(1+2*NODE_SIZE) + 2 + 3*(1+NODE_SIZE) + NODE_SIZE + 2*E*NODE_SIZE


def _sum(rows, width):
    rows = tuple(rows)
    return tuple(fsum(r[i] for r in rows) for i in range(width))


def _normalized(values):
    if any(not isfinite(v) for v in values):
        raise ValueError('Training features must be finite')
    return tuple(0.0 if v == 0 else copysign(log1p(abs(v)), v) for v in values)


def _node(state, index):
    if index == -1:
        return (0.,)*NODE_SIZE
    if not 0 <= index < len(state.cards)+len(state.players):
        raise ValueError('Encoder reference outside state rows')
    if index < len(state.cards):
        return (1., 0., *state.cards[index], *(0.,)*P)
    return (0., 1., *(0.,)*C, *state.players[index-len(state.cards)])


def state_vector(state):
    """!
    @brief Pool state features into the current numeric training schema.
    """
    if state.schema_version != SCHEMA_VERSION or len(state.players) != 2:
        raise ValueError('Expected encoder v1 and two players')
    result = [*state.globals, *state.players[0], *state.players[1]]
    for own in (True, False):
        rows = [r for r in state.cards if bool(r[1]) == own]
        result.extend((len(rows), *_sum(rows, C)))
    for kind in range(3):
        edges = [(a,b) for k,a,b in state.edges if k == kind]
        result.extend((len(edges), *_sum((_node(state,a) for a,b in edges), NODE_SIZE),
                       *_sum((_node(state,b) for a,b in edges), NODE_SIZE)))
    result.extend(_sum((row for _, row in state.card_effects), E))
    assert len(result) == STATE_SIZE
    return _normalized(result)


def action_vector(state, action):
    """!
    @brief Pool complete-action features into the current numeric training schema.
    """
    if action.schema_version != SCHEMA_VERSION:
        raise ValueError('Incompatible action encoder')
    result = [*action.features, *_node(state, action.source)]
    for cost in (True, False):
        result.extend(_sum((r for r in action.effects if bool(r[0]) == cost), E))
    for role in range(3):
        bindings = [(s,t,n) for r,g,s,t,n in action.bindings if r == role]
        result.extend((fsum(n for s,t,n in bindings),
            *_sum((tuple(n*x for x in _node(state,s)) for s,t,n in bindings), NODE_SIZE),
            *_sum((tuple(n*x for x in _node(state,t)) for s,t,n in bindings), NODE_SIZE)))
    result.extend(fsum(n for role,_,n in action.parameters if role == r) for r in range(2))
    for selection in action.selections:
        # Positional weights retain order, without treating physical row indices
        # as numeric features. Full graph/order expressiveness remains future work.
        result.extend((len(selection), *_sum((tuple((i+1)*x for x in _node(state,node))
                      for i,node in enumerate(selection)), NODE_SIZE)))
    result.extend(_sum((_node(state,i) for i in action.mana_sources), NODE_SIZE))
    # Effect x recipient interactions retain which effect addresses which target.
    for cost in (True, False):
        interactions = []
        for effect, slots in zip(action.effects, action.effect_slots):
            if bool(effect[0]) == cost:
                for role, group, source, target, count in action.bindings:
                    if role == (0 if cost else 1) and group in slots:
                        node = _node(state, target)
                        interactions.append(tuple(count*e*x for e in effect for x in node))
        result.extend(_sum(interactions, E*NODE_SIZE))
    assert len(result) == ACTION_SIZE
    return _normalized(result)


def encode_training(information):
    """!
    @brief Encode actor information and offered actions for the learner.
    """
    state, actions = ActionEncoder().encode_menu(information)
    return state_vector(state), tuple(action_vector(state,a) for a in actions)
