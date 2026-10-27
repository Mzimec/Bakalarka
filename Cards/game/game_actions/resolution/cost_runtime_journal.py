"""First-write snapshots of native cost containers in a proven card scope.

Scalar object fields are small shallow records. Zone memberships, tracked
modifier containers and mana pools are traversed only if a payment writes them.
Unknown implementations and nested observers keep eager snapshots.
"""
from helper.change_tracking import TrackedDict, TrackedSet
from ...game_state.battlefield import CardCollection, Battlefield
from ...mana.mana_value import ManaPool

_CONTAINERS = (CardCollection, Battlefield, TrackedDict, TrackedSet, ManaPool)


def unchanged_register_structure(state):
    """Native cost operations cannot register/unregister cards or players.

    Called only after card_write_scope proved the complete operation tree.
    Ordinary RuntimeCheckpoint clients retain their full graph boundary.
    """
    from helper.query_system.object_register import ObjectStorage, SimpleObjectIndexProvider
    from ..game_stack import GameStack
    from ...rules.combat import CombatState
    roots = []
    if (type(state.stack) is not GameStack or type(state.combat) is not CombatState
            or any(callable(value) for value in vars(state.stack).values())
            or any(callable(value) for value in vars(state.combat).values())
            or any(type(player.mana_pool) is not ManaPool for player in state.players)
            or any(callable(value) for player in state.players for value in vars(player.mana_pool).values())
            or any(type(zone) not in (CardCollection, Battlefield)
                   or any(callable(value) for value in vars(zone).values())
                   for player in state.players for zone in player._zone_map.values())):
        return ()
    if any(callable(value) for value in vars(state).values()) or any(
            callable(value) for player in state.players for value in vars(player).values()):
        return ()
    for register in (state.card_register, state.player_register):
        if (type(register.storage) is not ObjectStorage
                or type(register.idx_provider) is not SimpleObjectIndexProvider
                or vars(register.idx_provider).keys() != {"_data", "_ordered_keys"}
                or any(callable(value) for key, value in vars(register.storage).items()
                       if key != "__orig_class__")
                or any(callable(value) for value in vars(register).values())):
            return ()
        roots.extend((register._by_key, register.storage))
    for name in ("_introductions", "_assigned_command_ids", "_command_cards", "_reference_owners"):
        roots.append(getattr(state.card_register, name))
    return tuple(roots)


def defer_runtime_contents(checkpoint, value):
    cls = type(value)
    if cls not in _CONTAINERS:
        return None
    if "_cost_before_write" in vars(value):
        return None
    if cls in (CardCollection, Battlefield):
        data, attribute = value, None
    elif cls is TrackedDict:
        data, attribute = value.data, "data"
    elif cls is TrackedSet:
        data, attribute = value._values, "_values"
    elif cls is ManaPool:
        if any(name in vars(value) for name in (
                "add", "add_pair", "substract", "subtract", "clear", "remove", "remove_pair", "substract_pair")):
            return None
        data, attribute = value._data, "_data"
    else:
        return None
    if attribute is not None and type(data) not in (dict, set):
        return None
    # An eager empty record costs less than installing an observer. There is
    # no reachable content to discover, so first-write deferral cannot help.
    if not data:
        return None

    def capture():
        if attribute is None:
            # Cards themselves are explicitly watched using the proven scope.
            checkpoint._restore.append(("dict", value, dict(value)))
        else:
            checkpoint.watch(data, root=True)
        if vars(value).get("_cost_before_write") is capture:
            del value._cost_before_write

    value._cost_before_write = capture
    checkpoint._runtime_hooks.append((value, capture))
    return attribute or "__zone_memberships__"


def close_runtime_contents(checkpoint):
    for value, hook in checkpoint._runtime_hooks:
        if vars(value).get("_cost_before_write") is hook:
            del value._cost_before_write
    checkpoint._runtime_hooks.clear()
