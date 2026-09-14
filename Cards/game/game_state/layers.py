"""Layer ordering shared by continuous effects and per-characteristic modifiers."""

from .modifier import Modifier
from ..enums import Layer, ModifierType
from ..stat_type import (
    STAT_CONTROLLER,
    STAT_TYPES,
    STAT_SUBTYPES,
    STAT_ABILITIES,
    STAT_TRIGGERS,
    STAT_POWER,
    STAT_TOUGHNESS,
    STAT_KEYWORDS,
    STAT_COLORS,
    STAT_STATIC_ABILITIES,
    STAT_INTRINSIC_MANA,
    STAT_MANA_COST,
)
from dataclasses import dataclass


def modifier_layer(stat, modifier):
    """!
    @brief Determine the continuous-effect layer for one stat modifier.

    Explicitly layered modifiers override the default layer implied by the
    characteristic. Power/toughness modifiers keep their own P/T sublayer,
    while otherwise unmapped characteristics fall back to the rules layer.

    @param stat Characteristic being modified.
    @param modifier Modifier applied to that characteristic.
    @return Layer in which the modifier should be evaluated.
    """
    if isinstance(modifier, (LayeredModifier, SwitchPowerToughness)):
        return modifier.layer

    return {
        STAT_CONTROLLER: Layer.CONTROL,
        STAT_TYPES: Layer.TYPE,
        STAT_SUBTYPES: Layer.TYPE,
        STAT_COLORS: Layer.COLOR,
        STAT_ABILITIES: Layer.ABILITY,
        STAT_TRIGGERS: Layer.ABILITY,
        STAT_KEYWORDS: Layer.ABILITY,
        STAT_STATIC_ABILITIES: Layer.ABILITY,
        STAT_INTRINSIC_MANA: Layer.ABILITY,
        STAT_MANA_COST: Layer.RULES,
    }.get(
        stat,
        modifier.layer if stat in (STAT_POWER, STAT_TOUGHNESS) else Layer.RULES,
    )


@dataclass(frozen=True)
class LayeredModifier(Modifier):
    """!
    @brief Wrap a modifier with an explicitly assigned continuous-effect layer.

    The wrapped modifier retains its original behavior and transformation; only
    its layer placement is overridden.
    """

    modifier: Modifier
    effect_layer: Layer

    @property
    def layer(self):
        """!
        @brief Return the explicitly assigned layer.
        """
        return self.effect_layer

    @property
    def behavior(self):
        """!
        @brief Preserve the behavior classification of the wrapped modifier.
        """
        return self.modifier.behavior

    def modify(self, original):
        """!
        @brief Apply the wrapped modifier to the current characteristic value.

        @param original Current characteristic value.
        @return Modified value.
        """
        return self.modifier.modify(original)


@dataclass(frozen=True)
class SwitchPowerToughness(Modifier):
    """!
    @brief Represent a power/toughness switch in the dedicated P/T switch layer.

    Include the same modifier in both P/T entries of the effect definition. The
    modifier itself leaves the supplied value unchanged; the stat evaluator
    obtains the opposite pre-switch characteristic when this behavior is seen.
    """

    @property
    def layer(self):
        """!
        @brief Return the dedicated power/toughness switch layer.
        """
        return Layer.PT_SWITCH

    @property
    def behavior(self):
        """!
        @brief Identify this modifier as a power/toughness switch.
        """
        return ModifierType.SWITCH

    def modify(self, original):
        """!
        @brief Preserve the supplied value.

        Actual switching is handled by the stat evaluator using the opposite
        pre-switch characteristic.

        @param original Current characteristic value.
        @return `original` unchanged.
        """
        return original  # The stat evaluator reads the opposite pre-switch value.


def dependency_order(items, *, key, dependencies, timestamp):
    """!
    @brief Order items so dependencies precede dependents, with timestamp fallback.

    Items are initially ordered by timestamp. When dependencies exist, an item
    is eligible once every remaining dependency either forms part of a cycle
    with that item or has already been removed from the pending set.

    This means only members of a dependency cycle ignore that cycle; unrelated
    dependencies still constrain their relative order.

    @param items Items to order.
    @param key Function returning each item's stable dependency key.
    @param dependencies Function returning keys this item depends on.
    @param timestamp Function returning the timestamp ordering key.
    @return Iterator yielding items in resolved dependency order.
    """
    pending = sorted(items, key=timestamp)

    if not any(dependencies(item) for item in pending):
        yield from pending
        return

    while pending:
        # Dependencies on already-applied or absent items no longer constrain
        # the ordering among the remaining candidates.
        keys = {key(item) for item in pending}
        graph = {
            key(item): set(dependencies(item)) & keys
            for item in pending
        }

        def reaches(start, target, seen):
            """Return whether `start` can reach `target` in the pending graph."""
            if start == target:
                return True
            if start in seen:
                return False
            return any(
                reaches(other, target, seen | {start})
                for other in graph.get(start, ())
            )

        # An unresolved dependency normally blocks an item. It ceases to block
        # only when that dependency can reach back to the item, making both part
        # of the same dependency cycle.
        eligible = [
            item
            for item in pending
            if all(
                reaches(dep, key(item), set())
                for dep in graph[key(item)]
            )
        ]

        # `pending` remains timestamp-sorted, so the first eligible item is the
        # timestamp fallback whenever dependency rules do not distinguish them.
        chosen = eligible[0]
        pending.remove(chosen)
        yield chosen


def lose_all_abilities_modifiers():
    """!
    @brief Build modifiers that remove all modeled ability-like characteristics.

    Layer-6 removal covers activated abilities, triggered abilities, keywords,
    static abilities and automatically generated intrinsic mana abilities.

    @return Mapping of affected stat types to their clearing modifiers.
    """
    from .modifier import SetModifier

    return {
        STAT_ABILITIES: (SetModifier({}),),
        STAT_TRIGGERS: (SetModifier({}),),
        STAT_KEYWORDS: (SetModifier(frozenset()),),
        STAT_STATIC_ABILITIES: (SetModifier(frozenset()),),
        STAT_INTRINSIC_MANA: (SetModifier(False),),
    }