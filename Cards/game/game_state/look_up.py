"""Registry lookup results and named access to runtime objects."""

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
    """!
    @brief Interface for parsing user-facing lookup strings into key segments.
    """

    @abstractmethod
    def parse(self, cmd: str) -> list[str]:
        """!
        @brief Split a lookup command into normalized path components.

        @param cmd Lookup command supplied by the caller.
        @return Ordered lookup components.
        """
        pass


class KeyParser(StringParser):
    """!
    @brief Parser for dot-separated lowercase lookup keys.
    """

    def parse(self, cmd: str) -> list[str]:
        """!
        @brief Normalize and split a dot-separated lookup key.

        @param cmd Raw lookup string.
        @return Lowercase key components with surrounding whitespace removed.
        """
        key = cmd.strip().lower()
        return key.split(".")


@dataclass
class LookUpResult:
    """!
    @brief Accumulated result of hierarchical runtime-object lookup.

    Individual fields preserve each successfully resolved level while `final`
    points to the deepest object reached by the lookup.
    """

    success: bool = True
    error: str = ""

    final: Any | None = None

    player: Player | None = None
    zone: ZoneType | None = None
    card: Card | None = None
    ability: Ability | None = None

    @staticmethod
    def fail(message: str) -> LookUpResult:
        """!
        @brief Construct a failed lookup result.

        @param message Human-readable failure description.
        @return Failed `LookUpResult`.
        """
        return LookUpResult(success=False, error=message)


class LookUpSystem:
    """!
    @brief Resolve hierarchical keys into players, zones, cards and abilities.

    Lookup components are interpreted positionally as:

    `player.zone.card.ability`

    Shorter keys are valid and return the deepest successfully resolved object
    through `LookUpResult.final`.
    """

    def __init__(self) -> None:
        """!
        @brief Initialize the lookup parser and positional processing pipeline.
        """
        self.parser = KeyParser()
        self.process_fncs = [
            self._process_player_part,
            self._process_zone_part,
            self._process_card_part,
            self._process_ability_part,
        ]

    def lookup(self, key: str, state: State) -> LookUpResult:
        """!
        @brief Resolve a hierarchical lookup key against the current game state.

        Processing stops at the first invalid component and returns the partial
        result together with an error message.

        @param key Dot-separated lookup key.
        @param state Current game state.
        @return Successful or failed lookup result.
        """
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

    def _process_player_part(
        self,
        key_part: str,
        res: LookUpResult,
        state: State,
    ) -> None:
        """!
        @brief Resolve the first key component as a player index.

        @param key_part Player index encoded as text.
        @param res Result object being populated.
        @param state Current game state.
        """
        try:
            idx = int(key_part)
        except:
            res.success = False
            res.error = f"  '{key_part}' was not convertible to int."
            return

        if idx < 0 or idx >= len(state.players):
            res.success = False
            res.error = (
                f"  '{idx}' was out of bounds for state.players list. "
                f"Number of players is {len(state.players)}."
            )
            return

        res.player = state.players[idx]
        res.final = res.player

    def _process_zone_part(
        self,
        key_part: str,
        res: LookUpResult,
        state: State,
    ) -> None:
        """!
        @brief Resolve the second key component as a zone shorthand.

        Supported zone keys are `h`, `d`, `b`, `e` and `g`.

        @param key_part Zone shorthand.
        @param res Result object being populated.
        @param state Current game state.
        """
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
                res.error = (
                    f"  '{key_part}' is invalid key part for zone lookup. "
                    f"Valid key parts are: 'b', 'd', 'e', 'g', 'h'."
                )
                return

        res.final = res.zone

    def _process_card_part(
        self,
        key_part: str,
        res: LookUpResult,
        state: State,
    ) -> None:
        """!
        @brief Resolve the third key component as a card belonging to the player.

        The previously resolved zone is passed to the player's card lookup.

        @param key_part Card key/reference.
        @param res Result object being populated.
        @param state Current game state.
        """
        assert res.player is not None

        res.card = res.player.try_find_card(key_part, res.zone)

        if res.card is None:
            res.success = False
            res.error = f"  Card with key: '{key_part}' could not be found."
            return

        res.final = res.card

    def _process_ability_part(
        self,
        key_part: str,
        res: LookUpResult,
        state: State,
    ) -> None:
        """!
        @brief Resolve the fourth key component as an ability on the selected card.

        @param key_part Ability key.
        @param res Result object being populated.
        @param state Current game state.
        """
        assert res.card is not None

        res.ability = res.card.try_find_ability(key_part)

        if res.ability is None:
            res.success = False
            res.error = (
                f"  Ability with key: '{key_part}' could not be found "
                f"on card '{res.card}'."
            )
            return

        res.final = res.ability