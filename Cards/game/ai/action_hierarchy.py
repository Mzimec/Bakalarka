"""PUCT over semantic action prefixes, without executing partial engine actions.

The legal flat menu is supplied by the engine. Prefix nodes share statistics
across source/ability, parameters, targets and payment alternatives. They are
selection nodes, not extra simulator advances or game-depth increments. This
does not yet avoid enumerating the complete legal menu as XMage micro-choices do.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import fsum, sqrt
from random import Random

from game.ai.search_actions import PlayerAction, canonical_json


@dataclass(frozen=True)
class ActionPath:
    """!
    @brief Immutable semantic prefixes ending in one complete action identity.
    @var parts Ordered kind, source, parameters, targets, payment and leaf keys.
    """

    parts: tuple[tuple[str, object], ...]

    @classmethod
    def from_action(cls, action: PlayerAction) -> ActionPath:
        """!
        @brief Expose shared prefixes without executing a partial engine action.
        @param action Complete, visibility-filtered action from the legal menu.
        @return Path whose final key preserves every complete action choice.
        """
        data = action.describe()
        path = [("kind", action.kind)]
        if action.kind in {"AbilityAction", "ManaAbilityAction", "LandPlayAction"}:
            path.append(("source", canonical_json((data.get("source", data.get("card")),
                                                  data.get("action_key"), data.get("ability_key")))))
            for stage in ("parameters", "targets", "mana"):
                path.append((stage, canonical_json(tuple(
                    (data.get(part) or {}).get(stage) for part in ("cost", "effect")))))
        path.append(("complete", action.sort_key))
        return cls(tuple(path))


@dataclass
class HierarchyNode:
    """!
    @brief Mutable PUCT statistics for one semantic action prefix.
    @var prior Sum of complete-action prior probabilities below this prefix.
    @var visits Number of completed samples backed up through this prefix.
    @var total Sum of their values from the root player's perspective.
    @var remaining Complete descendant actions that have not been visited yet.
    @var action Complete action at a leaf, or None at an internal prefix.
    @var children Child prefixes in deterministic insertion order.
    """

    prior: float = 0.0
    visits: int = 0
    total: float = 0.0
    remaining: int = 0
    action: PlayerAction | None = None
    children: dict[object, HierarchyNode] = field(default_factory=dict)

    def score(self, exploration: float, parent_visits: int, total_prior: float,
              siblings: int, covering: bool) -> float:
        """!
        @brief Compute first-visit coverage priority or normalized PUCT score.
        @param covering Whether unvisited descendants must be covered first.
        @return Selection score from the root player's perspective.
        """
        prior = self.prior / total_prior if total_prior else 1 / siblings
        if covering:
            return prior * sqrt(1 + parent_visits) / (1 + self.visits)
        mean = self.total / self.visits if self.visits else 0.0
        return mean + exploration * prior * sqrt(max(1, parent_visits)) / (1 + self.visits)


class ActionHierarchy:
    """!
    @brief Own prefix selection and complete-action backpropagation paths.
    @var root Shared prefix above every offered action.
    @var paths Action-to-prefix paths used to update statistics exactly once.
    @var selection_steps Number of prefix edges selected during this search.
    @var node_count Number of owned prefix nodes, including the root.
    """

    def __init__(self, actions: Sequence[PlayerAction],
                 probabilities: Mapping[PlayerAction, float]) -> None:
        """!
        @brief Build deterministic prefixes and aggregate complete-action priors.
        @param actions Complete legal actions; generation remains authoritative.
        @param probabilities Prior probability for every supplied action.
        @throws ValueError If two actions have the same complete path.
        """
        self.root = HierarchyNode()
        self.paths: dict[PlayerAction, tuple[HierarchyNode, ...]] = {}
        self.selection_steps = 0
        for action in actions:
            node = self.root
            nodes = [node]
            for part in ActionPath.from_action(action).parts:
                node = node.children.setdefault(part, HierarchyNode())
                nodes.append(node)
            if node.action is not None:
                raise ValueError("Duplicate action in hierarchical menu")
            node.action = action
            self.paths[action] = tuple(nodes)
            for node in nodes:
                node.prior += probabilities[action]
                node.remaining += 1
        # Nodes may occur in multiple paths; count each owned prefix once.
        self.node_count = len({id(n) for path in self.paths.values() for n in path})

    def select(self, exploration: float, rng: Random, *, force_coverage: bool = True,
               allowed_actions: Sequence[PlayerAction] | None = None) -> tuple[PlayerAction, bool]:
        """!
        @brief Select a complete leaf within the current widening subset.
        @param rng Search-owned stream used to break equal-score ties.
        @param force_coverage Whether unvisited leaves receive first visits.
        @param allowed_actions Optional nonempty subset of the original menu.
        @return Selected action and whether it has no completed visits yet.
        @throws ValueError If the subset is empty or contains stale actions.
        """
        eligible = pending_nodes = None
        if allowed_actions is not None:
            if not allowed_actions or any(a not in self.paths for a in allowed_actions):
                raise ValueError("Choose a nonempty subset of the hierarchical menu")
            eligible = {id(n) for a in allowed_actions for n in self.paths[a]}
            pending_nodes = {id(n) for a in allowed_actions if self.paths[a][-1].visits == 0 for n in self.paths[a]}
        node = self.root
        while node.children:
            children = tuple(c for c in node.children.values() if eligible is None or id(c) in eligible)
            # Preserve full root coverage without flattening prefix selection.
            pending = tuple(c for c in children if (c.remaining if pending_nodes is None else id(c) in pending_nodes)) if force_coverage else ()
            choices = pending or children
            total_prior = fsum(c.prior for c in children)
            scores = [(c.score(exploration, node.visits, total_prior, len(children), bool(pending)), c)
                      for c in choices]
            maximum = max(s for s, _ in scores)
            node = rng.choice([c for s, c in scores if s == maximum])
            self.selection_steps += 1
        return node.action, node.visits == 0

    def record(self, action: PlayerAction, value: float) -> None:
        """!
        @brief Back up one completed trajectory through the selected action path.
        @param value Leaf value from the root player's perspective.
        """
        self.record_summary(action, 1, value)

    def record_summary(self, action: PlayerAction, visits: int, total: float) -> None:
        """!
        @brief Restore or accumulate completed statistics along an action path.
        @param visits Number of completed samples represented by total.
        @param total Sum of root-player sample values, not their mean.
        """
        if not visits:
            return
        nodes = self.paths[action]
        first = nodes[-1].visits == 0
        for node in nodes:
            node.visits += visits
            node.total += total
            if first:
                node.remaining -= 1
