from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from state import State
    from player import Player
    from card import Card 
    from ..abilities import Ability

from ..enums import *


class StringParser(ABC):

    @abstractmethod
    def parse(self, cmd: str) -> list[str]:
        pass


class KeyParser(StringParser):

    def parse(self, cmd: str) -> list[str]:
        key = cmd.strip().lower()
        return key.split(".")


@dataclass
class LookUpResult:
    success: bool = True
    error: str = ""

    final: Any | None = None

    player: Player | None = None
    zone: ZoneType | None = None
    card: Card | None = None
    ability: Ability | None = None 

    @staticmethod
    def fail(message: str) -> LookUpResult:
        return LookUpResult(success=False, error=message)


class LookUpSystem:
    
    def __init__(self) -> None:
        self.parser = KeyParser()
        self.process_fncs = [
            self._process_player_part,
            self._process_zone_part,
            self._process_card_part,
            self._process_ability_part
        ]

    def lookup(self, key: str, state: State) -> LookUpResult:
        parts = self.parser.parse(key)

        if len(parts) < 1:
            return LookUpResult.fail(f"  Key '{key}' was empty string.")
        
        if len(parts) > 4:
            return LookUpResult.fail(f"  Key '{key}' has too many parts.")
        
        res = LookUpResult()
        
        for i, p in enumerate(parts):
            self.process_fncs[i](p, res, state)
            if not res.success:
                return res
        
        return res

    def _process_player_part(self, key_part: str, res: LookUpResult, state: State) -> None:
        try:
            idx = int(key_part)
        except:
            res.success = False
            res.error = f"  '{key_part}' was not convertible to int."
            return
        
        if idx < 0 or idx >= len(state.players):
            res.success = False
            res.error = f"  '{idx}' was out of bounds for state.players list. Number of players is {len(state.players)}."
            return
        
        res.player = state.players[idx]
        res.final = res.player

    def _process_zone_part(self, key_part: str, res: LookUpResult, state: State) -> None:
        match key_part:
            case "h":
                res.zone = ZoneType.HAND
            case "d":
                res.zone = ZoneType.DECK
            case "b":
                res.zone = ZoneType.BATTLEFIELD
            case "e":
                res.zone = ZoneType.EXILE
            case "g":
                res.zone = ZoneType.GRAVEYARD
            case _:
                res.success = False
                res.error = f"  '{key_part}' is invalid key part for zone lookup. Valid key parts are: 'b', 'd', 'e', 'g', 'h'."
                return
        
        res.final = res.zone

    def _process_card_part(self, key_part: str, res: LookUpResult, state: State) -> None:
        assert res.player is not None
        res.card = res.player.try_find_card(key_part, res.zone)

        if res.card is None:
            res.success = False
            res.error = f"  Card with key: '{key_part}' could not be found."
            return
        
        res.final = res.card

    def _process_ability_part(self, key_part: str, res: LookUpResult, state: State) -> None:
        assert res.card is not None
        res.ability = res.card.try_find_ability(key_part)

        if res.ability is None:
            res.success = False
            res.error = f"  Ability with key: '{key_part}' could not be found on card '{res.card}'."
            return
        
        res.final = res.ability
