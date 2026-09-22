"""Player lookup and health queries."""

from helper.query_system.object_register import IndexKey
from .indexed_register import IndexedRegister

IK_NAME = IndexKey[str]("player.name")
IK_HEALTH = IndexKey[int]("player.health")
IK_HAS_LOST = IndexKey[bool]("player.has_lost")
IK_FAILED_DRAW = IndexKey[bool]("player.failed_draw")
IK_POISON = IndexKey[int]("player.poison")
IK_ALIVE = IndexKey[bool]("player.alive")


class PlayerRegister(IndexedRegister):
    """!
    @brief Indexed registry for runtime players.

    Provides lookup by normalized name and derived queries over current health
    and alive/dead state.
    """

    def __init__(self, state):
        """!
        @brief Initialize the player register.

        Health is kept as an ordered index so range-based health queries can be
        evaluated efficiently.

        @param state Owning game state.
        """
        super().__init__(
            state,
            (IK_NAME, IK_HEALTH, IK_ALIVE, IK_HAS_LOST, IK_FAILED_DRAW, IK_POISON),
            (IK_HEALTH, IK_POISON),
        )

    def index_values(self, player):
        """!
        @brief Compute the player's current query-index memberships.

        @param player Runtime player being indexed.
        @return Mapping from player index keys to immutable membership sets.
        """
        return {
            IK_HAS_LOST: frozenset({player.has_lost}),
            IK_FAILED_DRAW: frozenset({player.failed_draw}),
            IK_POISON: frozenset({player.poison_counters}),
            IK_NAME: frozenset({player.name.casefold()}),
            IK_HEALTH: frozenset({player.health}),
            IK_ALIVE: frozenset({player.is_alive}),
        }