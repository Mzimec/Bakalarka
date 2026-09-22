"""Resource symmetry for mana search; concrete source capacity is preserved."""
from collections import Counter
from dataclasses import dataclass
from game.game_actions.generation.equivalence import EquivalenceContext, UnknownSemantics


@dataclass
class ManaSearchStatistics:
    states_visited: int = 0
    equivalent_branches_skipped: int = 0


class ManaSymmetry:
    def __init__(self, state, sources, groups):
        context = EquivalenceContext(state)
        self.members = {}
        self.options = []
        self.resources = {}
        for index, (source, group) in enumerate(zip(sources, groups)):
            self.members.setdefault(group, []).append(index)
            card_key = context.card_key(source.source)
            try:
                option = (
                    source.ability_key if card_key[0] == "identity" else None,
                    context.structure.freeze(source.produces), context.structure.freeze(source.costs),
                    source.uses_remaining, source.output_per_activation,
                )
            except UnknownSemantics:
                option = ("unknown", index)
            self.options.append(option)
        for group, indices in self.members.items():
            source = sources[indices[0]].source
            card = context.card_key(source) if source is not None else ("pool", group)
            self.resources[group] = (card, frozenset(Counter(self.options[i] for i in indices).items()))

    def resource_state(self, group, remaining, group_remaining):
        return (self.resources[group], group_remaining[group], frozenset(Counter(
            (self.options[i], remaining[i]) for i in self.members[group]
        ).items()))

    def state_key(self, remaining, group_remaining):
        return frozenset(Counter(self.resource_state(g, remaining, group_remaining)
                                 for g in self.members).items())
