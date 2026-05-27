from game.target import TargetResolver, TargetSlot
from game.target.target_selector import SingleTargetSelector
from dummy_classes import StaticTargetSpec


def make_slot(slot_key: str, candidates, distinct_from: frozenset[str] = frozenset()):
    resolver = TargetResolver(
        target_spec=StaticTargetSpec(candidates),
        target_selector=SingleTargetSelector()
    )

    return TargetSlot(
        key=slot_key,
        target_resolver=resolver,
        distinct_from=distinct_from
    )