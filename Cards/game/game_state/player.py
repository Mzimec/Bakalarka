"""Player resources, owned card zones and decision-maker contracts."""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from abc import ABC, abstractmethod
from uuid import uuid4
from helper.runtime_object import RuntimeObject

from ..constants import STARTING_HEALTH, HAND_SIZE
from .battlefield import Battlefield, CardCollection

if TYPE_CHECKING:
    from ..game_actions.data_structs.ability import GameAction
    from .card import Card
    from .state import State
    from .battlefield import CardCollection
    from ..ai.decision_maker import DecisionMaker

from ..enums import *

__all__ = ["Player"]


class Player(RuntimeObject):
    """!
    @brief Runtime player state, owned zones and controller connection.

    A player owns the card collections for all zones, tracks player-local
    resources and delegates decisions to a `DecisionMaker`.
    """

    def __init__(
        self, deck: list[Card], controller: DecisionMaker, idx: int = 0, name: str | None = None
    ) -> None:
        """!
        @brief Create a player preserving the supplied deck order.

        Deck shuffling is intentionally handled by `game.game_loop.setup`.

        @param deck Cards that start in the player's deck.
        @param controller Decision maker that chooses this player's actions.
        @param idx Stable player index within the game.
        @param name Optional display name.
        """
        super().__init__()
        self._key = uuid4().hex
        self._game_state = None
        self._controller = controller
        self._health = STARTING_HEALTH
        from ..mana.mana_value import ManaPool

        self.mana_pool = ManaPool()
        self.lands_played_this_turn = 0
        self.land_plays_per_turn = 1
        self.last_turn_started = 0
        self.maximum_hand_size = HAND_SIZE
        self.failed_draw = False
        self.has_lost = False
        self.loss_reason = None
        self.poison_counters = 0
        self._idx = idx
        self._name = name if name is not None else f"Player {idx + 1}"

        self._zone_map: dict[ZoneType, CardCollection] = {
            zone: Battlefield() if zone == ZoneType.BATTLEFIELD else CardCollection()
            for zone in ZoneType
        }

        for card in deck:
            self.add_card(card, ZoneType.DECK)

    @property
    def key(self) -> str:
        return self._key

    @property
    def game_state(self):
        return self._game_state

    @property
    def idx(self) -> int:
        return self._idx

    @property
    def controller(self) -> DecisionMaker:
        return self._controller

    @controller.setter
    def controller(self, value: DecisionMaker) -> None:
        self._controller = value

    @property
    def decision_maker(self) -> DecisionMaker:
        """!
        @brief Alias exposing the player's controller as a decision maker.
        """
        return self.controller

    @property
    def name(self) -> str:
        return self._name

    def __str__(self) -> str:
        return self.name

    @property
    def health(self) -> int:
        return self._health

    @health.setter
    def health(self, value: int) -> None:
        """!
        @brief Update life total and invalidate the player query index.
        """
        self._health = value

        if self._game_state is not None:
            self._game_state.player_register.mark_changed(self)

    @property
    def is_alive(self):
        """!
        @brief Return whether the player is still active in the game.
        """
        return not self.has_lost and self.health > 0

    @property
    def deck(self) -> CardCollection:
        return self._zone_map[ZoneType.DECK]

    @property
    def hand(self) -> CardCollection:
        return self._zone_map[ZoneType.HAND]

    @property
    def battlefield(self) -> Battlefield:
        return self._zone_map[ZoneType.BATTLEFIELD]

    @property
    def graveyard(self) -> CardCollection:
        return self._zone_map[ZoneType.GRAVEYARD]

    @property
    def exile(self) -> CardCollection:
        return self._zone_map[ZoneType.EXILE]

    def get_zone(self, zone: ZoneType) -> CardCollection:
        """!
        @brief Return this player's collection for the requested zone.

        @param zone Zone to retrieve.
        @return Corresponding card collection.
        """
        return self._zone_map[zone]

    def add_card(
        self, card: Card, zone: ZoneType = ZoneType.DECK, state: State | None = None
    ) -> None:
        """!
        @brief Register an owned card in one of this player's zones.

        If the same runtime card is already present, the operation becomes a
        zone move instead. A different card using the same key is rejected.

        @param card Card to add.
        @param zone Destination zone.
        @param state Optional game state used to validate registration.
        @throws ValueError If the card belongs to another player or conflicts
            with an existing card key.
        """
        state = self._resolve_state(state)

        if card.owner is not self:
            raise ValueError("Cannot add a card owned by another player.")

        # Tokens that have already left the battlefield cease to exist rather
        # than entering another zone.
        if card.is_token and card.token_left_battlefield:
            return

        existing = self.try_find_card(card.key)

        if existing is not None:
            if existing is not card:
                raise ValueError(f"Duplicate card key: {card.key}")
            self.move_card(card, zone, state)
            return

        if state is not None:
            state.card_register.validate_new(card)

        self._zone_map[zone].append(card)
        card.set_zone(zone, state.time_stamp if state is not None else None)

        if state is not None:
            state.register_card(card)

    def move_card(self, card: Card, zone: ZoneType, state: State | None = None) -> None:
        """!
        @brief Move an existing card between this player's zone collections.

        Zone metadata is updated only after collection membership has been moved.

        @param card Card to move.
        @param zone Destination zone.
        @param state Optional bound game state.
        @throws ValueError If the card is not present or the destination already
            contains the same key.
        """
        state = self._resolve_state(state)

        if card.is_token and card.token_left_battlefield:
            return

        destination = self._zone_map[zone]

        source = next(
            (
                collection
                for collection in self._zone_map.values()
                if collection.get(card.key) is card
            ),
            None,
        )

        if source is None:
            raise ValueError(f"Card '{card.key}' is not in {self.name}'s zones.")

        if source is destination:
            return

        if card.key in destination:
            raise ValueError(f"Duplicate card key: {card.key}")

        source.pop(card.key)
        destination.append(card)
        card.set_zone(zone, state.time_stamp if state is not None else None)

    def _resolve_state(self, state):
        """!
        @brief Validate an optional state argument against this player's binding.

        The player always operates against its bound game state. A supplied
        different state is rejected rather than used transiently.
        """
        if state is not None and state is not self._game_state:
            raise ValueError("Player is not bound to the supplied game state.")

        return self._game_state

    def remove_card(self, card: Card) -> None:
        """!
        @brief Remove a card entity from the game entirely.

        This is distinct from moving a card to another zone: the card is also
        removed from the runtime register and detached from the game state.

        @param card Card to remove.
        @throws ValueError If the card is not present in this player's zones.
        """
        source = next(
            (zone for zone in self._zone_map.values() if zone.get(card.key) is card), None
        )

        if source is None:
            raise ValueError("Card is not owned by this player's collections.")

        if self._game_state is not None:
            self._game_state.card_register.unregister(card)

        source.pop(card.key)
        card._game_state = None

    def try_find_card(self, card_key: str, zone: ZoneType | None = None) -> Card | None:
        """!
        @brief Find a card by key, optionally restricted to one zone.

        @param card_key Runtime card key.
        @param zone Optional zone restriction.
        @return Matching card, or `None` if absent.
        """
        if zone is None:
            for z in self._zone_map.values():
                card = z.get(card_key)
                if card is not None:
                    return card

        else:
            return self._zone_map.get(zone).get(card_key)

        return None

    def get_cards(self, from_zones: list[ZoneType] | None = None) -> list[Card]:
        """!
        @brief Return cards owned by this player from selected zones.

        @param from_zones Zones to inspect, or all zones when omitted.
        @return Materialized list of matching cards.
        """
        if from_zones is None:
            from_zones = [z for z in ZoneType]

        cards: list[Card] = []

        for zone in from_zones:
            cards.extend(self._zone_map[zone].values())

        return cards

    def draw(self, state: State | None = None) -> Card:
        """!
        @brief Move the top card of the deck into the player's hand.

        The collection convention treats the last inserted deck entry as the top.

        @param state Optional bound game state.
        @return Drawn card.
        @throws ValueError If the deck cannot provide a card.
        """
        if len(self.deck) == 0:
            raise ValueError(f"Deck is empty on draw for {self}!")

        card = next(reversed(self.deck.values()))
        self.move_card(card, ZoneType.HAND, state)
        return card

    def get_action(self, state: State) -> GameAction | None:
        """!
        @brief Ask the player's controller to choose an action.

        @param state Current game state.
        @return Chosen action, or `None` if the player takes no action.
        """
        return self.controller.get_action(state, self)