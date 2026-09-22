
"""!
@brief Convenience adapters for generating or validating concrete ability actions.

This module provides a thin, user-facing layer over the lower-level
ability-action generation pipeline.

It is intended for two common call sites:

1. AI / exhaustive generation:
   The caller supplies only an ability and current state, and the helper
   enumerates every legal combination of modes, targets, costs, and mana
   activations.

2. Player-selected action validation:
   The caller supplies already chosen targets and optionally selected
   modes. The same generation machinery is then reused to verify that
   those supplied choices correspond to one legal concrete
   `AbilityAction`.

Keeping both cases on the same generation pipeline avoids duplicating
rules for targeting, modal selection, mana payment, and cost execution.
"""

from .subability_generator import FixedSubAbilityGenerator
from .generation_strategy import action_generation_strategy


def ability_actions(
    ability,
    state,
    targets=None,
    *,
    mode=None,
    cost_targets=(),
    cost_mode=None,
    x_value=0,
    life_payment=None,
):
    """!
    @brief Generate legal concrete actions for an ability using optional
           caller-supplied choices.

    Adapts a relatively simple public API (`ability`, selected targets,
    optional modes, X value, etc.) into the collection of generation
    strategies required by `AbilityActionGenerationPipeline`.

    The helper deliberately supports both exhaustive generation and
    validation of preselected player choices:

    - `targets is None` means that effect targets should be enumerated
      exhaustively using `FullTargetBindingGenerator`.
    - `targets` being a tuple (including an empty tuple) means that the
      caller has explicitly supplied the desired effect targets and only
      those bindings should be considered.
    - `mode is None` causes all legal effect-side action-node options to
      be generated.
    - `mode` being supplied restricts generation to that specific
      action-node option.
    - Cost targets are always treated as explicitly supplied through
      `ProvidedTargetBindingGenerator`. The default empty tuple therefore
      represents a cost that requires no caller-selected targets.
    - `cost_mode is None` permits all legal cost-side modes, while a
      concrete `cost_mode` restricts the cost pipeline to that choice.

    Mana payment is delegated to `SourceActivatingManaSolver`, allowing
    the generation pipeline to consider mana sources that must first be
    activated rather than relying only on mana already present in the
    player's pool.

    @param ability The runtime `Ability` whose legal concrete
           `AbilityAction` instances should be generated.
    @param state Current game state used for legality checks, target
           generation, mana solving, and effect validation.
    @param targets Optional caller-selected targets for the main effect.
           If `None`, all legal target bindings are enumerated. If a
           tuple is supplied, only that target selection is validated.
    @param mode Optional selected action-node option for the main effect.
           If `None`, all legal effect modes/options are considered.
    @param cost_targets Explicit targets/resources selected for paying
           non-mana costs. Defaults to an empty tuple.
    @param cost_mode Optional selected action-node option for the cost
           side of the ability. If `None`, all legal cost options are
           considered.
    @param x_value Chosen value for X/Y-style variable costs or effects.
           Forwarded unchanged to `AbilityActionGenerationPipeline`.
    @param life_payment Optional life payment associated with the action
           (for example Phyrexian-style mana payment). Forwarded to the
           shared generation context.
    @return Iterator yielding every legal `AbilityAction` compatible
            with the supplied restrictions.
    @throws ValueError Propagates legality or validation errors raised
            by the underlying generation pipeline.
    """

    strategy = action_generation_strategy(
        subability_gen=FixedSubAbilityGenerator(),
        targets=targets, cost_targets=cost_targets, mode=mode, cost_mode=cost_mode,
    )

    # Delegate all legality checks and cartesian composition of
    # sub-abilities, cost plans, and effect plans to the canonical
    # generation pipeline.
    yield from ability.generate_actions(
        strategy,
        state,
        x_value=x_value,
        life_payment=life_payment,
    )


def chosen_action(ability, state, targets=(), **choices_kwargs):
    """!
    @brief Resolve one fully selected player choice into exactly one legal action.

    Uses `ability_actions` in restricted/validation mode by converting
    the supplied `targets` to a tuple and asking the normal generation
    pipeline for matching actions.

    This helper is appropriate when the caller expects the user's input
    to identify one unambiguous concrete `AbilityAction`.

    A missing result means the supplied targets/modes/cost choices are
    illegal in the current state. More than one result means the caller
    has not specified enough information to uniquely identify an action;
    currently this is reported as requiring an explicit mode choice.

    @param ability Runtime `Ability` being activated or cast.
    @param state Current game state against which the supplied choices
           are validated.
    @param targets Explicit main-effect target selection. Defaults to an
           empty tuple for abilities that require no targets.
    @param choices_kwargs Additional selections forwarded to
           `ability_actions`, such as `mode`, `cost_targets`,
           `cost_mode`, `x_value`, or `life_payment`.
    @return The single legal `AbilityAction` matching the supplied
            player choices.
    @throws ValueError If no legal action matches the supplied choices,
            or if the choices are still ambiguous and produce more than
            one legal action.
    """

    # Supplying a tuple switches `ability_actions` from exhaustive target
    # generation to validation of exactly the caller-provided target set.
    choices = ability_actions(
        ability,
        state,
        tuple(targets),
        **choices_kwargs,
    )

    # The first generated result is the candidate concrete action.
    action = next(choices, None)

    if action is None:
        # No generation result means at least one supplied choice
        # (targets, mode, cost targets, mana payment, etc.) cannot form a
        # legal action in the current state.
        raise ValueError("No legal action for the supplied choices.")

    # A fully specified player action should map to exactly one concrete
    # result. A second result indicates that some branch of generation
    # is still ambiguous, typically an unselected modal option.
    if next(choices, None) is not None:
        raise ValueError("This ability requires an explicit mode choice.")

    return action

