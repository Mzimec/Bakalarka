"""Flat PUCT selection over completed, actor-information edge statistics.

This component sees only the currently eligible actions, their normalized
priors and completed rewards. It owns no world, observation, tree or RNG.
Menu generation, widening and backpropagation remain with the search driver.
"""
from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True, slots=True)
class PUCTSelector:
    """!
    @brief Select by Q(a) + c * P(a) * sqrt(max(1, N)) / (1 + n(a)).
    @var exploration Nonnegative exploration coefficient c.
    @var force_initial_coverage Visit every eligible action before score selection.

    Q is the completed root-player reward mean, or zero for an unvisited
    edge. N includes all completed visits of the information node, including
    actions outside the current widening subset. Priors retain the original
    full-menu normalization. Neither an interrupted nor a merely selected
    trajectory contributes a visit here.
    """

    exploration: float
    force_initial_coverage: bool = True

    def __post_init__(self):
        if not isfinite(self.exploration) or self.exploration < 0:
            raise ValueError('exploration must be finite and nonnegative')
        if type(self.force_initial_coverage) is not bool:
            raise ValueError('force_initial_coverage must be boolean')

    def select(self, actions, rows, probabilities, rng):
        """!
        @brief Return an eligible action and whether it has no completed visits.
        @param actions Nonempty, ordered widening subset of the legal menu.
        @param rows Edge table with maintained completed parent visits.
        @param probabilities Validated, normalized priors for the full menu.
        @param rng Search-owned tie-breaking stream; consumed exactly once.

        Scan scores once and retain only equal maxima instead of allocating a
        (score, action) tuple for every edge. Preserve menu order among ties
        and call choice even for a single winner, matching the search stream.
        No counter or input container is mutated.
        """
        if not actions:
            raise ValueError('PUCT needs a nonempty eligible menu')
        best = -float('inf')
        winners = []
        if self.force_initial_coverage:
            for action in actions:
                if rows[action].visits:
                    continue
                prior = probabilities[action]
                if prior > best:
                    best = prior
                    winners.clear()
                    winners.append(action)
                elif prior == best:
                    winners.append(action)
            if winners:
                return rng.choice(winners), True

        base = sqrt(max(1, rows.visits))
        exploration = self.exploration
        for action in actions:
            stats = rows[action]
            visits = stats.visits
            mean = stats.total / visits if visits else 0.0
            # Keep the original arithmetic order: reassociation could change
            # floating-point ties and consequently the seeded search path.
            bonus = probabilities[action] * base / (1 + visits)
            score = mean + exploration * bonus
            if score > best:
                best = score
                winners.clear()
                winners.append(action)
            elif score == best:
                winners.append(action)
        action = rng.choice(winners)
        return action, rows[action].visits == 0
