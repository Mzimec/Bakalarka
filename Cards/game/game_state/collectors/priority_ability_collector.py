"""Compose the established land and activated/spell discovery components."""
from __future__ import annotations
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING
from .ability_collector import AbilityCollector
from .land_play_collector import LandPlayCollector

if TYPE_CHECKING:
    from ..state import State
    from ..player import Player
    from ...game_actions.data_structs.ability import Ability, PlayLandAbility


class PriorityAbilitySource(Protocol):
    def collect_lands(self, state: State, player: Player) -> Iterator[PlayLandAbility]: ...
    def collect_abilities(self, state: State, player: Player, *, include_mana: bool) -> Iterator[Ability]: ...


@dataclass(frozen=True)
class PriorityAbilityCollector:
    """Discovery only: each existing collector owns its query and permission rules."""
    lands: LandPlayCollector = field(default_factory=LandPlayCollector)
    abilities: AbilityCollector = field(default_factory=AbilityCollector)

    def collect_lands(self, state: State, player: Player) -> Iterator[PlayLandAbility]:
        return self.lands.collect(state, player)

    def collect_abilities(self, state: State, player: Player, *, include_mana: bool = True) -> Iterator[Ability]:
        collect = self.abilities.collect if include_mana else self.abilities.collect_non_mana
        return collect(state, player)


PRIORITY_ABILITY_COLLECTOR = PriorityAbilityCollector()
