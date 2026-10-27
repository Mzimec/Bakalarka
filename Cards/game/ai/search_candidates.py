"""Deterministic, observation-only progressive widening of a legal menu.

This limits search allocation, not legality or menu generation. Source-first
generation is a separate protocol. Every action eventually becomes eligible;
pass is retained from the first allocation, within the configured width.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil

from game.ai.search_actions import PlayerAction
from game.ai.search_recall import PlayerInformation


class CandidateOrdering:
    """!
    @brief Rank a legal menu using only acting-player information or given priors.
    @var policy Pure observation-only scorer used when probabilities are absent.
    """

    def __init__(self, policy=None) -> None:
        """!
        @brief Bind a reusable pure scorer; this object never owns a native world.
        """
        from game.ai.observation_policy import ObservationOpponentPolicy
        self.policy = policy if policy is not None else ObservationOpponentPolicy()

    def order(self, information: PlayerInformation,
              probabilities: Mapping[PlayerAction, float] | None = None) -> tuple[PlayerAction, ...]:
        """!
        @brief Put pass first, then rank by score and canonical action content.
        @param information Acting player's facts, recall and offered legal menu.
        @param probabilities Optional complete prior map instead of local ratings.
        @return Stable order independent of native option enumeration.
        """
        scores = ({row.action: row.score for row in self.policy.rank(information)}
                  if probabilities is None else probabilities)
        return tuple(sorted(information.actions, key=lambda action: (
            action.kind != "PassPriorityAction", -scores[action], action.sort_key)))


@dataclass(frozen=True)
class ProgressiveWidening:
    """!
    @brief Immutable visit-to-width schedule; it never changes action legality.
    @var constant Multiplicative coefficient in ceil(c * (visits + 1) ** exponent).
    @var exponent Growth rate configured for this search.
    """

    constant: float
    exponent: float

    def width(self, visits: int, *, minimum_constant: float = 0.0) -> int:
        """!
        @brief Compute the admitted prefix width before applying a menu-size cap.
        @param visits Completed visits of this information node.
        @param minimum_constant Floor used by native candidate-frontier expansion.
        @return At least one candidate, preserving the configured widening formula.
        """
        return max(1, ceil(max(minimum_constant, self.constant) * (visits + 1) ** self.exponent))

    def select(self, order: Sequence[PlayerAction], visits: int) -> Sequence[PlayerAction]:
        """!
        @brief Admit a monotone prefix of an already ranked legal menu.
        @return Prefix preserving pass and the relative order of admitted actions.
        """
        return order[:min(len(order), self.width(visits))]
