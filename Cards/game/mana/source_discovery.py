"""Recognise deterministic tap-for-mana abilities supported by auto-payment."""

from itertools import islice

from .mana_generator import ManaSource
from .mana_value import ManaValue, ColoredSymbol
from ..enums import ZoneType
from ..game_actions.card_effects import TapSourceEffect
from ..game_actions.mana_effects import AddManaEffect
from ..game_actions.data_structs.ability import SubAbilityComposer, SubAbilityDefinition, ManaAbilityDefinition
from ..game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from .discovery_context import current_discovery_context
from .source_template import static_mana_template, EMPTY_MANA


def _static_sequence(definitions):
    """Only immutable declarative leaves can be shared between searches."""
    return (type(definitions) is tuple and all(
        type(part) is SubAbilityDefinition and part.mana_cost is None
        and type(part.effects) is frozenset and type(part.slots) is frozenset
        and not part.slots and type(part.action_node) is EffectActionNode
        and type(part.action_node.effect_map) is ImmutableEffectToSlotMap
        and all(type(slots) is frozenset and not slots for slots in part.action_node.effect_map.values())
        for part in definitions
    ))


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
    # A plain, untargeted leaf already describes its only possible sequence.
    # Read it directly instead of cloning a graph and allocating options and
    # bindings. Exact types keep custom compilation/generation on the general
    # path; dynamic effect amounts are still evaluated by mana_sources below.
    if type(definitions) is tuple and len(definitions) == 1:
        definition = definitions[0]
        if type(definition) is SubAbilityDefinition:
            node = definition.action_node
            if (definition.mana_cost is None and not definition.slots
                    and type(node) is EffectActionNode
                    and type(node.effect_map) is ImmutableEffectToSlotMap
                    and not any(node.effect_map.values())):
                effects = {effect.key: effect for effect in definition.effects}
                return tuple(effects[key] for key in node.effect_map)

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

    context = current_discovery_context()
    state.card_register.synchronise()
    if context is not None:
        cached = context.lookup(state, player)
        if cached is not None:
            return cached

    persistent = state._mana_source_cache
    cached = persistent.lookup(state, player)
    if cached is not None:
        if context is not None:
            entry = persistent.entries[player]
            return context.remember(state, player, entry.cards, entry.effects, cached)
        return cached

    result = []
    cacheable = context is not None
    dependencies = []
    persistent_cacheable = persistent.supported(state)
    definitions, templates = [], []

    # Ownership determines zone storage; control determines who may activate.
    from helper.query_system.query import EqQuery
    from ..game_state.registers.card_register import IK_ZONE, IK_CONTROLLER, IK_ABILITY_KIND
    from ..enums import ActivatableAbilityType

    candidates = state.query_cards(
        EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
        & EqQuery(IK_CONTROLLER, player)
        & EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.MANA)
    )
    for card in candidates:
        if (cacheable or persistent_cacheable) and not state._card_snapshots.has_current_index_projection(state, card):
            cacheable = persistent_cacheable = False

        for key, definition in card.get_ability_defs(state).items():
            if persistent_cacheable:
                definitions.append(definition)
            # Variable subabilities and stack-using abilities cannot be treated
            # as one deterministic mana-source activation.
            if not definition.is_mana_ability or definition.subdefs or definition.uses_stack:
                if definition.is_mana_ability:
                    cacheable = False
                    persistent_cacheable = False
                continue

            template = static_mana_template(definition)
            if template is not None:
                if persistent_cacheable:
                    templates.append(template)
                if cacheable:
                    dependencies.extend(template.costs)
                    dependencies.extend(template.effects)
                result.append(ManaSource(card, key, template.produces, EMPTY_MANA, 1,
                                         output_per_activation=True))
                continue

            persistent_cacheable = False

            if cacheable:
                cacheable = (type(definition) is ManaAbilityDefinition
                             and _static_sequence(definition.cost_subdefs)
                             and _static_sequence(definition.action_subdefs)
                             and all(type(effect) is TapSourceEffect for part in definition.cost_subdefs for effect in part.effects)
                             and all(type(effect) is AddManaEffect and type(effect.amount) is int
                                     for part in definition.action_subdefs for effect in part.effects))
                if cacheable:
                    dependencies.extend(effect for part in (*definition.cost_subdefs, *definition.action_subdefs) for effect in part.effects)

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

            resolution_context = ResolutionContext(source=card, controller=player, ability=definition)

            # Resolve dynamic amounts once in the current state. Discovery is
            # accepted only when every output becomes a concrete nonnegative int.
            amounts = [effect.get_amount(state, resolution_context) for effect in effects]

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

    result = context.remember(state, player, candidates, dependencies, result) if cacheable else tuple(result)
    if persistent_cacheable:
        persistent.remember(state, player, candidates, definitions, templates, result)
    else:
        persistent.entries.pop(player, None)
    return result
