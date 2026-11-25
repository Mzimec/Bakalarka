"""Plan indivisible mana outputs without activating a permanent more than once."""

from collections import Counter

from .mana_value import ImmutableManaPool, generate_mana_options_from_req_pool
from ..enums import ManaType


def plan_bundles(requirement, sources, pool, *, state=None, deduplicate_equivalent=True, statistics=None):
    """!
    @brief Find a minimum-size activation plan for deterministic mana bundles.

    Mana outputs produced by one activation are treated as indivisible bundles.
    Multiple color choices belonging to the same permanent are grouped as
    alternatives, which prevents the search from activating that permanent more
    than once.

    The search is iterative by activation count, so the first successful plan
    uses the smallest possible number of source activations.

    @param requirement Mana requirement that must be payable.
    @param sources Available deterministic mana-source options.
    @param pool Mana already present before activating additional sources.
    @return A `ManaPlan` containing activation steps and exact payment, or
        `None` if the requirement cannot be satisfied.

    Any mana produced beyond the selected payment is intentionally preserved for
    real execution.
    """
    from .mana_generator import ManaPlan, ManaPlanStep

    # All activation alternatives belonging to one permanent form one group.
    # Selecting any option from a group consumes that permanent for this plan.
    grouped = {}

    for source in sources:
        output = Counter()

        for symbol in source.produces.symbols:
            if len(symbol.mana) != 1:
                raise ValueError("A bundled output must choose its mana color explicitly.")

            output[next(iter(symbol.mana))] += 1

        output[ManaType.COLORLESS] += source.produces.generic
        grouped.setdefault(source.source, []).append((source, output))

    groups = list(grouped.values())

    # Try constrained/small-choice groups first. For equal alternative counts,
    # prefer groups capable of producing the larger bundle.
    groups.sort(
        key=lambda group: (
            len(group),
            -max(sum(output.values()) for _, output in group),
        )
    )

    from .symmetry import ManaSymmetry, ManaSearchStatistics
    statistics = statistics if statistics is not None else ManaSearchStatistics()
    symmetry = ManaSymmetry(state, sources, [s.source for s in sources]) if deduplicate_equivalent else None
    colors = tuple(ManaType)
    total = requirement.cmc()
    failed = set()

    def search(index, available, steps, remaining):
        """!
        @brief Search for a valid payment using at most `remaining` activations.

        @param index First source group still eligible for selection.
        @param available Mana currently available to the hypothetical payment.
        @param steps Activation steps already chosen.
        @param remaining Number of additional activations still allowed.
        @return A complete `ManaPlan`, or `None` if this branch cannot succeed.
        """
        statistics.states_visited += 1
        payment = next(
            generate_mana_options_from_req_pool(
                requirement,
                ImmutableManaPool(available),
            ),
            None,
        )

        if payment is not None:
            return ManaPlan(tuple(steps), payment)

        if remaining == 0 or index == len(groups):
            return None

        # Mana above the total requirement is irrelevant to future feasibility,
        # so cap bucket counts to merge equivalent failed search states.
        fingerprint = (
            index,
            remaining,
            tuple(min(total, available.get(color, 0)) for color in colors),
        )

        if fingerprint in failed:
            return None

        # Optimistic upper bound: assume every remaining permanent contributes
        # its best output independently for each color. If even this cannot pay
        # the requirement, the current branch cannot possibly succeed.
        upper = Counter(available)

        for group in groups[index:]:
            for color in colors:
                upper[color] += max(output.get(color, 0) for _, output in group)

        if (
            next(
                generate_mana_options_from_req_pool(
                    requirement,
                    ImmutableManaPool(upper),
                ),
                None,
            )
            is None
        ):
            failed.add(fingerprint)
            return None

        seen_groups = set()
        for next_index in range(index, len(groups)):
            if symmetry is not None:
                key = symmetry.resources[groups[next_index][0][0].source]
                if key in seen_groups:
                    statistics.equivalent_branches_skipped += 1
                    continue
                seen_groups.add(key)
            seen_outputs = set()
            # Only one alternative from this group can be selected. Recursive
            # search resumes after the group, preventing reuse of its permanent.
            for source, output in groups[next_index]:
                output_key = (source.ability_key, frozenset(output.items()))
                if symmetry is not None and output_key in seen_outputs:
                    statistics.equivalent_branches_skipped += 1
                    continue
                seen_outputs.add(output_key)
                result = search(
                    next_index + 1,
                    Counter(available) + output,
                    [
                        *steps,
                        ManaPlanStep(
                            source.source,
                            source.ability_key,
                            next(color for color in colors if output[color]),
                        ),
                    ],
                    remaining - 1,
                )

                if result is not None:
                    return result

        failed.add(fingerprint)
        return None

    # Iterative deepening guarantees that the first solution minimizes the
    # number of activated permanents.
    for count in range(len(groups) + 1):
        result = search(0, Counter(pool), [], count)

        if result is not None:
            return result

    return None