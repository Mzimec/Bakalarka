"""Composable graphs of effects, costs and target-slot requirements."""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, override, Self
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Iterator
from immutabledict import immutabledict

if TYPE_CHECKING:
    from ...mana.mana_value import ManaRequirement, ImmutableManaRequirement


from ...enums import *
from helper.dict_helper import MutableSetMapping

__all__ = ["EffectToSlotMap", "ActionNode", "AndActionNode", "OrActionNode", "EffectActionNode"]

EffectKey = str
SlotKey = str


class EffectToSlotMap(MutableSetMapping[EffectKey, SlotKey]):
    """!
    @brief Mutable mapping from an effect key to the set of target slot keys it uses.

    Built up while assembling action options (e.g. while merging
    `AndActionNode` children); convert to `ImmutableEffectToSlotMap` via
    `to_immutable` once the mapping is finalized, so it can be safely
    stored on a frozen `ActionNodeOption`.
    """

    def to_immutable(self) -> ImmutableEffectToSlotMap:
        """!
        @brief Return an immutable representation of this value.
        """
        return ImmutableEffectToSlotMap({k: frozenset(v) for k, v in self.items()})


class ImmutableEffectToSlotMap(immutabledict[EffectKey, frozenset[SlotKey]]):
    """!
    @brief Frozen counterpart of `EffectToSlotMap`, safe to hash/store on frozen dataclasses.
    """

    def to_mutable(self) -> EffectToSlotMap:
        """!
        @brief Return a mutable representation of this value.
        """
        return EffectToSlotMap({k: set(v) for k, v in self.items()})


@dataclass(frozen=True)
class ActionNodeOption:
    """!
    @brief One concrete, fully-resolved way to use an `ActionNode`.

    Represents a single combination of "which effects use which target
    slots" together with the mana requirement it implies, as produced
    by `ActionNode.generate_options`. Since it is a frozen dataclass
    over hashable immutable fields, distinct options can be deduplicated
    via a `set`.

    @var effects
        Immutable mapping from effect key to the target slot keys that
        feed it in this option.
    @var mana_req
        The mana requirement implied by this option (non-empty only for
        options that include a `ManaActionNode` contribution).
    """

    effects: ImmutableEffectToSlotMap
    mana_req: ImmutableManaRequirement


class ActionNode(ABC):
    """!
    @brief Base node for describing which effects an ability can use.

    Action nodes form a small tree/graph describing the possible ways
    an ability's cost or effect portion can be resolved — e.g. "all of
    these effects together" (`AndActionNode`), "any one of these
    alternatives" (`OrActionNode`), a single concrete effect-to-slot
    binding (`EffectActionNode`), or a pure mana requirement
    (`ManaActionNode`). `generate_options` enumerates every concrete way
    the subtree can be resolved.
    """

    @abstractmethod
    def generate_options(self) -> Iterator[ActionNodeOption]:
        """!
        @brief Return every possible effect-to-target-slot mapping for this node.
        @return A list of possible mappings from effect keys to target slot keys.
        """
        ...

    @abstractmethod
    def create_with_sufix(self, sufix: str) -> Self:
        """!
        @brief Return a copy of this node with all slot keys disambiguated by a suffix.

        Used when the same `ActionNode` is reused multiple times within
        one compiled ability (e.g. via `SubAbilityComposer`, when a
        sub-definition is added more than once), so that repeated
        occurrences don't collide on identical slot keys.

        @param sufix String suffix to append to every slot key introduced
               by this node (and, recursively, by its children).
        @return A new node of the same kind with suffixed slot keys.
        """
        ...


@dataclass(frozen=True)
class AndActionNode(ActionNode):
    """!
    @brief Combines child nodes so all of them must be present in one action option.

    Every generated option pairs one option from each child together,
    merging their effect-to-slot maps (effects appearing in multiple
    children have their slot sets unioned) and summing their mana
    requirements. This is the node used, for example, to combine
    multiple simultaneous sub-abilities into a single ability
    (`SubAbilityComposer._compile_action_graph`).

    @var children
        The child nodes that must all contribute to every generated
        option.
    """

    children: tuple[ActionNode, ...]

    def _combine_lazy(
        self, child_idx: int, effects: EffectToSlotMap, mana_req: ImmutableManaRequirement
    ) -> Generator[ActionNodeOption, None, None]:
        """!
        @brief Recursively builds the cartesian product of all children's options.

        Walks `self.children` one at a time starting at `child_idx`,
        trying every option of the current child and recursing with the
        accumulated, merged `effects` map and `mana_req`. Once every
        child has been processed (`child_idx >= len(self.children)`),
        yields the fully accumulated combination as one
        `ActionNodeOption`.

        @param child_idx Index of the child currently being expanded.
        @param effects Mutable effect-to-slot map accumulated so far from
               already-processed children.
        @param mana_req Mana requirement accumulated so far from
               already-processed children.
        @return Generator of `ActionNodeOption`s, one per combination of
                child options explored so far along this recursion path.
        """
        if child_idx >= len(self.children):
            yield ActionNodeOption(effects.to_immutable(), mana_req)
            return

        from ...mana.mana_value import ImmutableManaRequirement

        current_child = self.children[child_idx]
        for option in current_child.generate_options():
            # Keep zero-target effects in the map, and isolate sibling choices.
            # A fresh copy is made per sibling option so branches explored
            # in the recursion below don't leak mutations into each other.
            merged = {key: set(slots) for key, slots in effects.items()}
            for key, slots in option.effects.items():
                merged.setdefault(key, set()).update(slots)
            mana = dict(mana_req)
            for symbol, amount in option.mana_req.items():
                mana[symbol] = mana.get(symbol, 0) + amount
            yield from self._combine_lazy(
                child_idx + 1, EffectToSlotMap(merged), ImmutableManaRequirement(mana)
            )

    @override
    def generate_options(self):
        """!
        @brief Build all combinations of child mappings and merge their slot usage.
        @return All merged effect-to-slot mappings produced by the children.
        """
        seen: set[ActionNodeOption] = set()
        from ...mana.mana_value import ImmutableManaRequirement

        # Deduplicate: different combinations of child options can
        # coincidentally produce the identical merged option.
        for option in self._combine_lazy(0, EffectToSlotMap(), ImmutableManaRequirement()):
            if option in seen:
                continue
            seen.add(option)
            yield option

    @override
    def create_with_sufix(self, sufix):
        """!
        @brief Returns a copy with the suffix applied recursively to every child.
        @param sufix String suffix to propagate to all children.
        @return A new `AndActionNode` wrapping the suffixed children.
        """
        return AndActionNode(
            children=tuple([child.create_with_sufix(sufix) for child in self.children])
        )


@dataclass(frozen=True)
class OrActionNode(ActionNode):
    """!
    @brief Represents a choice where any one child action option may be used.

    Unlike `AndActionNode`, options are not combined — each option
    produced by any child is yielded independently, representing "pick
    one of these alternatives" (e.g. modal spells, or "choose one"
    abilities).

    @var children
        The alternative child nodes, any one of whose options may be
        chosen.
    """

    children: tuple[ActionNode, ...]

    @override
    def generate_options(self):
        """!
        @brief Collect all action options produced by the alternative child nodes.
        @return The union of all child effect-to-slot mappings.
        """
        seen: set[ActionNodeOption] = set()
        for child in self.children:
            for option in child.generate_options():
                if option in seen:
                    continue
                seen.add(option)
                yield option

    @override
    def create_with_sufix(self, sufix):
        """!
        @brief Returns a copy with the suffix applied recursively to every child.
        @param sufix String suffix to propagate to all children.
        @return A new `OrActionNode` wrapping the suffixed children.
        """
        return OrActionNode(
            children=tuple([child.create_with_sufix(sufix) for child in self.children])
        )


@dataclass(frozen=True)
class EffectActionNode(ActionNode):
    """!
    @brief Leaf node that connects one effect key to the slots it needs.

    The simplest kind of action node: a fixed, single mapping from
    effect key(s) to the target slot key(s) each needs, with no
    alternatives and no mana requirement of its own.

    @var effect_map
        Immutable mapping from effect key to the target slot keys it
        consumes.
    """

    effect_map: ImmutableEffectToSlotMap

    @override
    def generate_options(self):
        """!
        @brief Return this single effect binding as one possible action option.
        @return A single mapping containing this effect and its target slots.
        """
        from ...mana.mana_value import ImmutableManaRequirement

        yield ActionNodeOption(effects=self.effect_map, mana_req=ImmutableManaRequirement())

    @override
    def create_with_sufix(self, sufix):
        """!
        @brief Returns a copy with `sufix` appended to every slot key in `effect_map`.
        @param sufix String suffix to append to each target slot key.
        @return A new `EffectActionNode` with suffixed slot keys (the
                effect keys themselves are left unchanged).
        """
        return EffectActionNode(
            effect_map=ImmutableEffectToSlotMap(
                {ek: frozenset({sk + sufix for sk in v}) for ek, v in self.effect_map.items()}
            )
        )


@dataclass(frozen=True)
class ManaActionNode(ActionNode):
    """!
    @brief Leaf node that contributes a mana requirement but no effect.
        Generated by ManaCost.to_action_node().

    @var mana_req
        The mana requirement this leaf contributes when included in an
        option.
    """

    mana_req: ImmutableManaRequirement

    @override
    def generate_options(self):
        """!
        @brief Yield distinct effect and target-slot options from this action node.

        Always yields exactly one option: no effects/slots, just this
        node's `mana_req`.
        """
        yield ActionNodeOption(effects=ImmutableEffectToSlotMap(), mana_req=self.mana_req)

    @override
    def create_with_sufix(self, sufix):
        """!
        @brief No-op: mana requirements carry no slot keys, so nothing needs suffixing.
        @param sufix Unused.
        @return `self`, unchanged.
        """
        return self