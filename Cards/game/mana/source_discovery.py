"""Recognise deterministic tap-for-mana abilities supported by auto-payment."""

from itertools import islice

from .mana_generator import ManaSource
from .mana_value import ManaValue, ColoredSymbol
from ..enums import ZoneType
from ..game_actions.card_effects import TapSourceEffect
from ..game_actions.mana_effects import AddManaEffect
from ..game_actions.data_structs.ability import SubAbilityComposer


def single_sequence(definitions, ability, state):
    """!
    @brief Reduce a subability definition set to one deterministic effect sequence.

    Auto-payment only accepts definitions that compile successfully, require no
    mana, expose exactly one action-node option, require no additional mana in
    that option, and use no target slots.

    @param definitions Subability definitions to compile.
    @param ability Runtime ability used as the compilation context.
    @param state Current game state.
    @return Tuple of effects in execution order, or `None` if the definition
        cannot be represented as one deterministic untargeted sequence.
    """
    part = SubAbilityComposer(definitions).compile(ability, state)

    if part is None or part.mana_cost or part.action_node is None:
        return None

    # Reading at most two options is enough to distinguish a unique sequence
    # from any action node that exposes alternatives.
    options = list(islice(part.action_node.generate_options(), 2))

    if len(options) != 1 or options[0].mana_req:
        return None

    effects = part.normalize_effect_map(options[0].effects)

    # Source discovery deliberately rejects effects requiring runtime target
    # selection; auto-payment must know the complete activation in advance.
    if effects.get_used_slots():
        return None

    return tuple(binding.effect for binding in effects.sequence)


def mana_sources(state, player):
    """!
    @brief Discover deterministic battlefield mana abilities usable by auto-payment.

    Only non-stack mana abilities with exactly one tap-source cost and one or
    more unmodified `AddManaEffect` outputs are recognized. This intentionally
    excludes abilities whose output depends on targets, choices, custom
    operation generation, additional costs, or other runtime decisions.

    @param state Current game state.
    @param player Player whose controlled mana sources should be discovered.
    @return Tuple of deterministic `ManaSource` descriptions.
    """
    if player not in state.players:
        return ()

    # Ability definitions and controllers may themselves be affected by
    # continuous effects, so discovery must operate on refreshed characteristics.
    state.refresh_continuous_effects()

    result = []

    # Ownership determines zone storage; control determines who may activate.
    for card in state.get_cards(from_zones=[ZoneType.BATTLEFIELD]):
        if card.get_controller(state) is not player:
            continue

        for key, definition in card.get_ability_defs(state).items():
            # Variable subabilities and stack-using abilities cannot be treated
            # as one deterministic mana-source activation.
            if not definition.is_mana_ability or definition.subdefs or definition.uses_stack:
                continue

            ability = definition.to_ability(card, player)

            costs = single_sequence(definition.cost_subdefs, ability, state)

            # Auto-payment currently recognizes exactly "tap this source" as
            # the complete non-mana activation cost.
            if costs is None or len(costs) != 1 or type(costs[0]) is not TapSourceEffect:
                continue

            effects = single_sequence(definition.action_subdefs, ability, state)

            # Require ordinary AddManaEffect instances whose operation generation
            # has not been overridden by a subclass.
            if not effects or any(
                not isinstance(effect, AddManaEffect)
                or type(effect).to_operations is not AddManaEffect.to_operations
                for effect in effects
            ):
                continue

            from ..game_actions.data_structs.game_action import ResolutionContext

            context = ResolutionContext(source=card, controller=player, ability=definition)

            # Resolve dynamic amounts once in the current state. Discovery is
            # accepted only when every output becomes a concrete nonnegative int.
            amounts = [effect.get_amount(state, context) for effect in effects]

            if any(type(amount) is not int or amount < 0 for amount in amounts) or sum(amounts) < 1:
                continue

            produced = ManaValue()

            for effect, amount in zip(effects, amounts):
                produced.add_pair(ColoredSymbol(frozenset({effect.mana})), amount)

            # The whole produced ManaValue belongs to one activation and must
            # therefore be consumed as an indivisible bundle by the planner.
            result.append(
                ManaSource(card, key, produced, ManaValue(), 1, output_per_activation=True)
            )

    return tuple(result)