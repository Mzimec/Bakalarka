"""Temporary visibility proofs for one synchronous multi-player projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.ai.observation import PlayerObservationBuilder
    from game.ai.structured_values import Value
    from game.game_state.card import Card


@dataclass(frozen=True, slots=True)
class VisibilityRead:
    """!
    @brief Record one viewer-dependent lookup made while projecting a card row.
    @var gate Name of the reviewed visibility method on the observation builder.
    @var subject Runtime object inspected only within this synchronous projection.
    @var result Detached lookup result that the next viewer must reproduce.
    """

    gate: str
    subject: object
    result: object

    def matches(self, builder: PlayerObservationBuilder) -> bool:
        """!
        @brief Recheck this permission for another viewer of the unchanged world.
        @return Whether reusing the original projected fact preserves visibility.
        """
        return getattr(builder, self.gate)(self.subject) == self.result


@dataclass(frozen=True, slots=True)
class DescriptionProjection:
    """!
    @brief Memoized rule description with its transitive visibility dependencies.
    @var source Retained source object preventing temporary identity reuse.
    @var value Detached description produced by the current observation builder.
    @var reads Dependencies to propagate when this description is reused.
    """

    source: object
    value: object
    reads: tuple[VisibilityRead, ...]


@dataclass(frozen=True, slots=True)
class ProjectedCardRow:
    """!
    @brief Frozen visible card facts guarded by one projection's permission reads.
    @var card Source identity; never included in the portable frozen value.
    @var value Complete immutable player-facing card row.
    @var reads All visibility lookups, including negative filters and nested LKI.

    This record is temporary. It must not survive a world mutation or escape
    into player information, retained search nodes or process payloads.
    """

    card: Card
    value: Value
    reads: tuple[VisibilityRead, ...]

    def matches(self, card: Card, builder: PlayerObservationBuilder) -> bool:
        """!
        @brief Check source identity and every permission before sharing a row.
        @return True only for the same card and equivalent visible facts.
        """
        return self.card is card and all(read.matches(builder) for read in self.reads)
