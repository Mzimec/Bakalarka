from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from ...abilities.ability import TriggerCondition

if TYPE_CHECKING:
    from ...abilities import Ability

@dataclass(frozen=True)
class TriggerAbility:
    """!
    @brief Pairing of a trigger condition with the ability it enables.
    """

    condition: TriggerCondition
    ability: Ability

