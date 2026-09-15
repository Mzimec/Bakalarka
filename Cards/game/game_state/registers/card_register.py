"""Indexes of live cards, using the shared bitset query engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from helper.query_system.object_register import IndexKey
from .indexed_register import IndexedRegister
from ...enums import CardType, CardSubtype, ZoneType, ActivatableAbilityType

if TYPE_CHECKING:
    from ..card import Card

IK_KEY = IndexKey[str]("card.key")
IK_NAME = IndexKey[str]("card.name")
IK_OWNER = IndexKey("card.owner")
IK_CONTROLLER = IndexKey("card.controller")
IK_CMC = IndexKey[int]("card.cmc")
IK_ZONE = IndexKey[ZoneType]("card.zone")
IK_TYPE = IndexKey[CardType]("card.type")
IK_SUBTYPE = IndexKey[CardSubtype]("card.subtype")
IK_POWER = IndexKey[int]("card.power")
IK_TOUGHNESS = IndexKey[int]("card.toughness")
IK_TAPPED = IndexKey[bool]("card.tapped")
IK_ABILITY_KIND = IndexKey[ActivatableAbilityType]("card.ability")

CARD_INDEX_KEYS = (
    IK_KEY,
    IK_NAME,
    IK_OWNER,
    IK_CONTROLLER,
    IK_CMC,
    IK_ZONE,
    IK_TYPE,
    IK_SUBTYPE,
    IK_POWER,
    IK_TOUGHNESS,
    IK_TAPPED,
    IK_ABILITY_KIND,
)


class CardRegister(IndexedRegister):
    """!
    @brief Indexed registry for all live runtime cards.

    Besides the shared query indexes, the register assigns each card a stable
    human-facing command reference such as `c1`. These references are preserved
    for a card identity even if the card is temporarily unregistered.
    """

    def __init__(self, state):
        """!
        @brief Initialize the card register and its command-reference namespace.

        @param state Owning game state.
        """
        super().__init__(
            state,
            CARD_INDEX_KEYS,
            (IK_CMC, IK_POWER, IK_TOUGHNESS),
        )
        self._next_command_id = 1
        self._command_cards = {}
        self._assigned_command_ids = {}
        self._reference_owners = {}

    def validate_new(self, card):
        """!
        @brief Validate that a card can be added to this register.

        In addition to the base entity-key checks, normal card keys may not
        collide with command references that have already been assigned.

        @param card Runtime card being registered.
        @throws ValueError If its key conflicts with an existing card or command ID.
        """
        super().validate_new(card)

        if (
            card.key in self._reference_owners
            and self._reference_owners[card.key] is not card
        ):
            raise ValueError(
                f"Card key '{card.key}' conflicts with an existing command ID."
            )

    def register(self, card):
        """!
        @brief Register a card and assign or restore its stable command reference.

        @param card Runtime card to register.
        """
        self.validate_new(card)
        super().register(card)

        identity = id(card)

        if identity not in self._assigned_command_ids:
            # Card keys and command IDs share lookup syntax, so skip any
            # generated reference already occupied by a real card key.
            while self.get_by_key(f"c{self._next_command_id}") is not None:
                self._next_command_id += 1

            reference = f"c{self._next_command_id}"
            self._next_command_id += 1

            # Retain the object together with its reference so Python cannot
            # recycle this identity and accidentally inherit another card's ID.
            self._assigned_command_ids[identity] = (card, reference)

        _, reference = self._assigned_command_ids[identity]

        card.command_id = reference
        self._command_cards[reference] = card
        self._reference_owners[reference] = card

    def get_by_reference(self, reference):
        """!
        @brief Resolve either a command ID or a normal card key.

        Command references take precedence over ordinary entity-key lookup.

        @param reference Command reference or card key.
        @return Matching card, or `None` if no card is registered under it.
        """
        return self._command_cards.get(reference) or self.get_by_key(reference)

    def unregister(self, card):
        """!
        @brief Remove a card from the live register.

        Its historical command-ID assignment is retained so registering the
        same runtime card again restores the same reference.

        @param card Runtime card to unregister.
        """
        super().unregister(card)
        self._command_cards.pop(card.command_id, None)

    def index_values(self, card: Card):
        """!
        @brief Compute all query-index memberships for the current card state.

        Values are derived through the card's public characteristic accessors so
        continuous effects and controller/type/stat modifications are reflected
        in the index snapshot.

        Optional characteristics such as power and toughness contribute no
        membership when they are not defined.

        @param card Runtime card being indexed.
        @return Mapping from card index keys to immutable membership sets.
        """
        cmc = card.get_mana_value(self.state)
        power, toughness = (
            card.get_power(self.state),
            card.get_toughness(self.state),
        )

        ability_defs = card.get_activatable_ability_defs(self.state)

        ability_types = frozenset(
            ActivatableAbilityType.MANA
            if definition.is_mana_ability
            else ActivatableAbilityType.NON_MANA
            for definition in ability_defs.values()
        )

        return {
            IK_KEY: frozenset({card.key}),
            IK_NAME: frozenset({card.name.casefold()}),
            IK_OWNER: frozenset({card.owner}),
            IK_CONTROLLER: frozenset({card.get_controller(self.state)}),
            IK_ZONE: frozenset({card.get_zone()}),
            IK_TYPE: frozenset(card.get_types(self.state)),
            IK_SUBTYPE: frozenset(card.get_subtypes(self.state)),
            IK_CMC: frozenset({cmc}),
            IK_POWER: frozenset() if power is None else frozenset({power}),
            IK_TOUGHNESS: frozenset() if toughness is None else frozenset({toughness}),
            IK_TAPPED: frozenset({card.is_tapped}),
            IK_ABILITY_KIND: ability_types,
        }