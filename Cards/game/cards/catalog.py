"""Combined executable catalog; starter-only APIs retain their original scope."""

from game.cards.starter_cards import starter_catalog
from game.cards.control_cards import control_catalog


def game_catalog():
    """!
    @brief Resolve supported cards from every implemented card collection.
    """
    return {**starter_catalog(), **control_catalog()}
