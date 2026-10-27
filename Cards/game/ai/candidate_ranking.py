"""Bounded, stable proposal ordering; not an exhaustive global top-k sort."""
from itertools import islice


FAMILIES = frozenset(('priority', 'ability', 'attackers', 'blockers', 'other'))


def rank_window(value):
    """!
    @brief Select the configured bounded proposal window for this request family.
    """
    if type(value) is not int or (value != 0 and value < 2):
        raise ValueError('Candidate rank windows must be zero (disabled) or integers >= 2')
    return value


def normalize_rank_windows(value):
    """!
    @brief Own an immutable copy, also accepting dataclass/JSON round trips.
    """
    if not isinstance(value, (dict, tuple, list)):
        raise ValueError('candidate_rank_windows must map generation families to windows')
    pairs = tuple(value.items()) if isinstance(value, dict) else tuple(value)
    result = {}
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError('Invalid candidate rank window entry')
        family, window = pair
        if type(family) is not str or family not in FAMILIES or family in result:
            raise ValueError('Unknown or duplicate candidate generation family')
        result[family] = rank_window(window)
    return tuple(sorted(result.items()))


def generation_family(request):
    """!
    @brief Classify a native request for request-specific candidate ranking.
    """
    kind = type(request).__name__
    if kind == 'PriorityDecisionRequest':
        return 'priority'
    if kind in ('AbilityDecisionRequest', 'PriorityAbilityRequest',
                'AbilityConstructionRequest', 'AbilityResolutionRequest'):
        return 'ability'
    if kind == 'DeclareAttackersRequest':
        return 'attackers'
    if kind == 'DeclareBlockersRequest':
        return 'blockers'
    return 'other'


def ranked_batches(stream, window, rank):
    """!
    @brief Sort fixed-size legal proposal batches without discarding their tail.

    Batch boundaries never depend on the requested frontier width. Replaying
    a larger width therefore preserves the entire previously admitted prefix.
    The caller still needs an explicit native work/option budget: a bounded
    number of legal proposals need not have a bounded legality-checking cost.
    """
    rank_window(window)
    stream = iter(stream)
    try:
        if not window:
            yield from stream
            return
        while batch := tuple(islice(stream, window)):
            yield from rank(batch) if len(batch) > 1 else batch
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            close()


class VisibleCandidateRanker:
    """!
    @brief Memoryless proposal heuristic using the existing actor-only adapters.

    Real recall is unchanged. Search/prior/opponent policies continue receiving
    their normal complete recall after admission. No extra world is cloned.
    """
    def __init__(self, request):
        self.request, self.view = request, None

    def __call__(self, options):
        from game.ai.observation_policy import ObservationOpponentPolicy
        from game.ai.search_decision import DecisionProjector
        from game.ai.search_recall import PlayerRecall
        projected = DecisionProjector(self.request, options, PlayerRecall(),
                                     view=self.view, retain_view=True).build()
        self.view = projected._view
        ratings = ObservationOpponentPolicy().rank(projected.information)
        # Keep pass available in the first priority batch. Remaining proposals
        # are ordered by the reviewed visible heuristic and canonical ties.
        ratings = sorted(ratings, key=lambda r: (
            r.action.kind != 'PassPriorityAction', -r.score, r.action.sort_key))
        return tuple(projected.bind(r.action) for r in ratings)
