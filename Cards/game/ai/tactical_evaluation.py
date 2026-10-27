"""Marginal tactical value; never remove a legal option from the engine."""
from game.game_actions.permanent_effects import AuraCastAbilityDefinition, AttachSourceEffect
from game.ai.visible_effects import restriction_keywords


def redundant_restriction_aura(state, action):
    """!
    @brief Recognize pure combat-restriction auras whose host has every restriction.

    Additional modifiers/effects or source abilities retain general evaluation.
    This is a prior, not a dominance proof: cast/ETB triggers or removal redundancy
    can still make the retained option useful to a search/model selector.
    """
    if type(action.ability) is not AuraCastAbilityDefinition:
        return False
    source = action.source
    if (source.definition.triggers or source.definition.continuous_effects
            or source.definition.replacement_effects
            or len(source.get_ability_defs(state)) != 1):
        return False
    restrictions = restriction_keywords(source.modifiers_to_attach(state))
    if not restrictions:
        return False
    effects = action.action_generator.effects.sequence
    if not effects or any(type(binding.effect) is not AttachSourceEffect for binding in effects):
        return False
    targets = [target for groups in action.action_generator.binding.values()
               for group in groups.values() for target in group]
    return bool(targets) and all(
        all(target.has_keyword(state, keyword) for keyword in restrictions) for target in targets)
