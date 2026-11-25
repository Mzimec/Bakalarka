"""Select a legal ability before expanding all of its concrete actions.

This opt-in protocol changes action construction, never execution. A source
candidate has one proven legal witness. Only the selected source is exhausted;
costs, targets and mana still use the established ability generation pipeline.
"""
from dataclasses import dataclass, field

from ...data_structs.game_action import GameAction
from .requests import AbilityDecisionRequest


@dataclass(frozen=True)
class PrioritySourceOption(GameAction):
    ability: object
    witness: GameAction
    _policy: object = field(repr=False, compare=False)
    _construction: object = field(default=None, repr=False, compare=False)

    def get_intents(self):
        raise ValueError("An ability source must be completed before execution")


@dataclass(frozen=True)
class PriorityAbilityRequest(AbilityDecisionRequest):
    """Complete one source selection without returning priority or paying costs."""
    _policy: object = field(default=None, repr=False, compare=False)

    def option_space(self, policy=None):
        return super().option_space(self._policy if policy is None else policy)


def complete_priority_source(request, option, decide):
    if option.ability.controller is not request.player:
        raise ValueError("The selected ability must belong to the acting player")
    if option._construction is not None:
        from .ability_frames import choose_construction
        return choose_construction(request, option._construction, decide)
    # The witness iterator was closed before offering this candidate. Rebuild
    # the selected source; no suspended generation context escapes the menu.
    return decide(PriorityAbilityRequest(request.state, request.player, option.ability, _policy=option._policy))
