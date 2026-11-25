"""Reviewed declarative effect features, with no execution or callback inspection.

These are heuristic inputs, not an alternative rules engine. Unknown effects
remain explicitly unknown, even when their text or key looks familiar.
"""


def effect_features(effect):
    """!
    @brief Describe reviewed effect semantics; unknown effects remain explicit.
    """
    from game.game_actions.card_effects import DamagePlayerEffect, MoveSourceEffect, TapSourceEffect
    from game.game_actions.advanced_effects import DrawEffect
    from game.game_actions.control_effects import CounterSpellEffect, DestroyAllCreaturesEffect
    from game.game_actions.permanent_effects import AttachSourceEffect
    from game.cards.starter_cards import DamageTargetEffect, DestroyEffect, DrawCardsEffect, GainLifeEffect

    kind = type(effect)
    if kind in (DamagePlayerEffect, DamageTargetEffect):
        return {"kind": "damage", "amount": effect.amount, "slot": effect.slot_key}
    if kind is DestroyEffect:
        return {"kind": "destroy", "slot": effect.slot_key}
    if kind is CounterSpellEffect:
        return {"kind": "counter", "slot": effect.slot_key}
    if kind is DestroyAllCreaturesEffect:
        return {"kind": "destroy_creatures"}
    if kind in (DrawEffect, DrawCardsEffect):
        return {"kind": "draw", "amount": effect.count if kind is DrawCardsEffect else 1}
    if kind is GainLifeEffect:
        return {"kind": "gain_life", "amount": effect.amount}
    if kind is MoveSourceEffect:
        return {"kind": "move_source", "destination": effect.destination.name}
    if kind is TapSourceEffect:
        return {"kind": "tap_source"}
    if kind is AttachSourceEffect:
        return {"kind": "attach", "slot": effect.slot_key}
    return {"kind": "unknown"}


def restriction_keywords(modifiers):
    """!
    @brief Pure restriction-only attachments; mixed buffs retain general scoring.
    """
    from game.game_state.modifier import AddSetModifier
    from game.stat_type import STAT_KEYWORDS
    if set(modifiers) != {STAT_KEYWORDS}:
        return ()
    keywords = set()
    for modifier in modifiers[STAT_KEYWORDS]:
        if type(modifier) is not AddSetModifier:
            return ()
        keywords.update(modifier.value)
    return tuple(sorted(keywords)) if keywords <= {"can't attack", "can't block"} else ()
