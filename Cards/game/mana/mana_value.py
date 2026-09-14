"""Shared mana mappings, symbols and exact payment planning (CR 106, 107, 202)."""

from __future__ import annotations
from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from immutabledict import immutabledict
from helper.dict_helper import MutablePositiveCounterMapping
from ..enums import ManaType

ALL_MANA = frozenset(ManaType)
MANA_LETTERS = dict(
    zip(
        "CWUBRG",
        (
            ManaType.COLORLESS,
            ManaType.WHITE,
            ManaType.BLUE,
            ManaType.BLACK,
            ManaType.RED,
            ManaType.GREEN,
        ),
    )
)


def _positive_counts(values):
    """!
    @brief Normalize a mana-count mapping to positive integer entries only.

    @param values Mapping-like source, or `None`.
    @return Dictionary containing only nonzero counts.
    @throws ValueError If any count is negative, boolean, or non-integer.
    """
    data = dict(values or {})
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in data.values()
    ):
        raise ValueError("Mana amounts must be nonnegative integers.")
    return {key: count for key, count in data.items() if count}


class ManaPoolBase(Mapping[ManaType, int], ABC):
    """!
    @brief Shared read-only interface for mutable and immutable mana pools.
    """

    def amount(self):
        """!
        @brief Return the total amount of mana currently stored.
        """
        return sum(self.values())


class ManaPool(MutablePositiveCounterMapping[ManaType], ManaPoolBase):
    """!
    @brief Mutable pool of currently available mana by mana type.
    """

    def __init__(self, values=None):
        super().__init__(_positive_counts(values))

    def to_immutable(self):
        """!
        @brief Return an immutable snapshot of this mana pool.
        """
        return ImmutableManaPool(self)

    def clear(self):
        """!
        @brief Remove all mana from the pool.
        """
        self._data.clear()

    def add(self, other):
        """!
        @brief Add mana counts from another mapping.
        """
        for mana, count in _positive_counts(other).items():
            self.add_pair(mana, count)

    def add_pair(self, mana, amount):
        """!
        @brief Add one mana-type/count pair to the pool.

        @throws ValueError If the amount is invalid or the key is not a
            `ManaType`.
        """
        _positive_counts({mana: amount})
        if not isinstance(mana, ManaType):
            raise ValueError("A pool contains mana types, not cost symbols.")
        super().add_pair(mana, amount)

    def substract(self, other):
        """!
        @brief Spend an exact mana payment from this pool.

        The operation validates the complete payment before mutating the pool.

        @param other Mapping from mana types to amounts to spend.
        @throws ValueError If the pool cannot cover the requested payment.
        """
        payment = _positive_counts(other)
        if any(self.get(mana, 0) < amount for mana, amount in payment.items()):
            raise ValueError("Insufficient mana for this payment.")

        for mana, amount in payment.items():
            remaining = self._data[mana] - amount
            if remaining:
                self._data[mana] = remaining
            else:
                self._data.pop(mana)

    def substract_pair(self, mana, amount):
        """!
        @brief Spend one mana-type/count pair from the pool.
        """
        self.substract({mana: amount})

    subtract = substract
    remove = substract

    def remove_pair(self, mana, amount):
        """!
        @brief Alias for spending one mana-type/count pair.
        """
        self.substract({mana: amount})


class ImmutableManaPool(immutabledict[ManaType, int], ManaPoolBase):
    """!
    @brief Immutable mana-pool representation used by planners and stat values.
    """

    def __new__(cls, values=None):
        return super().__new__(cls, _positive_counts(values))

    def to_mutable(self):
        """!
        @brief Return a mutable copy of this mana pool.
        """
        return ManaPool(self)


@dataclass(frozen=True)
class ManaRequirementFragment:
    """!
    @brief One homogeneous part of a mana requirement.

    `allowed` contains every mana type that may satisfy each of `amount`
    interchangeable units of this fragment.
    """

    allowed: frozenset[ManaType]
    amount: int = 1

    def __post_init__(self):
        _positive_counts({self.allowed: self.amount})


class ManaRequirementBase(Mapping[frozenset[ManaType], int], ABC):
    """!
    @brief Shared mapping interface for compiled mana requirements.
    """

    def cmc(self):
        """!
        @brief Return the total number of mana units required.
        """
        return sum(self.values())

    @property
    def frags(self):
        """!
        @brief Materialize mapping entries as requirement-fragment objects.
        """
        return tuple(ManaRequirementFragment(key, count) for key, count in self.items())


class ManaRequirement(MutablePositiveCounterMapping[frozenset[ManaType]], ManaRequirementBase):
    """!
    @brief Mutable collection of mana requirements grouped by allowed types.
    """

    def __init__(self, values=None, *, frags=()):
        if values is not None and not isinstance(values, Mapping):
            values = tuple(values)
            if values and isinstance(values[0], ManaRequirementFragment):
                frags, values = values, None

        super().__init__(_positive_counts(values))

        for fragment in frags:
            self.add_pair(fragment.allowed, fragment.amount)

    @classmethod
    def empty(cls):
        """!
        @brief Construct an empty mana requirement.
        """
        return cls()

    def to_immutable(self):
        """!
        @brief Return an immutable representation of this requirement.
        """
        return ImmutableManaRequirement(self)


class ImmutableManaRequirement(immutabledict[frozenset[ManaType], int], ManaRequirementBase):
    """!
    @brief Immutable compiled mana requirement.
    """

    def __new__(cls, values=None):
        return super().__new__(cls, _positive_counts(values))

    def to_mutable(self):
        """!
        @brief Return a mutable copy of this requirement.
        """
        return ManaRequirement(self)


@dataclass(frozen=True)
class ManaSymbol(ABC):
    """!
    @brief Base type for one mana-cost symbol.

    `cmc` stores the symbol's default mana-value contribution.
    """

    cmc: int = field(default=1, init=False)


@dataclass(frozen=True)
class DeterministicSymbol(ManaSymbol):
    """!
    @brief Symbol payable by any mana type in a fixed allowed set.
    """

    allowed: frozenset[ManaType]

    @property
    def mana(self):
        return self.allowed


@dataclass(frozen=True)
class ColoredSymbol(ManaSymbol):
    """!
    @brief Ordinary colored or hybrid-colored mana symbol.
    """

    mana: frozenset[ManaType]

    @property
    def allowed(self):
        return self.mana


@dataclass(frozen=True)
class GenericSymbol(ManaSymbol):
    """!
    @brief Generic mana symbol payable by any mana type.
    """

    pass


@dataclass(frozen=True)
class VariableSymbol(ManaSymbol):
    """!
    @brief Variable mana symbol such as X.

    Variable symbols contribute zero to mana value except while evaluating a
    spell on the stack with an explicitly chosen X value.
    """

    name: str = "X"
    cmc: int = field(default=0, init=False)


@dataclass(frozen=True)
class HybridGenericSymbol(ManaSymbol):
    """!
    @brief Hybrid symbol payable either with one specific color or two generic mana.
    """

    mana: ManaType
    cmc: int = field(default=2, init=False)


@dataclass(frozen=True)
class PhyrexianSymbol(ManaSymbol):
    """!
    @brief Phyrexian symbol payable either with allowed mana or two life.
    """

    allowed: frozenset[ManaType]


@dataclass(frozen=True)
class GeneralisedSymbol(ManaSymbol):
    """!
    @brief Nonstandard cost symbol compiled into a subability definition.
    """

    subdef: object


def format_mana_cost(value):
    """!
    @brief Render a mana cost using familiar notation such as `{2}{U}` or `{W/P}`.

    @param value Mana value to format, or `None` for no mana cost.
    @return Human-readable mana-cost notation.
    """
    if value is None:
        return "none"

    letters = {mana: letter for letter, mana in MANA_LETTERS.items()}
    parts = []

    for symbol, count in value.items():
        if isinstance(symbol, GenericSymbol):
            parts.append("{" + str(count) + "}")
            continue

        if isinstance(symbol, VariableSymbol):
            label = symbol.name
        elif isinstance(symbol, HybridGenericSymbol):
            label = "2/" + letters[symbol.mana]
        else:
            label = "/".join(
                letter for letter, mana in MANA_LETTERS.items() if mana in symbol.allowed
            )
            if isinstance(symbol, PhyrexianSymbol):
                label += "/P"

        parts.extend(["{" + label + "}"] * count)

    return "".join(parts) or "{0}"


class ManaValueBase(Mapping[ManaSymbol, int], ABC):
    """!
    @brief Shared interface for mutable and immutable mana costs.
    """

    def cmc(self, x_value=0):
        """!
        @brief Return this cost's mana value.

        @param x_value Value substituted for variable symbols.
        """
        return sum(
            (x_value if isinstance(symbol, VariableSymbol) else symbol.cmc) * count
            for symbol, count in self.items()
        )

    @property
    def generic(self):
        """!
        @brief Return the number of generic mana symbols.
        """
        return self.get(GenericSymbol(), 0)

    @property
    def symbols(self):
        """!
        @brief Expand all non-generic symbols according to their multiplicity.
        """
        return tuple(
            symbol
            for symbol, count in self.items()
            if not isinstance(symbol, GenericSymbol)
            for _ in range(count)
        )

    @property
    def has_x(self):
        """!
        @brief Return whether the cost contains any variable mana symbol.
        """
        return any(isinstance(symbol, VariableSymbol) for symbol in self)

    def payment_options(self, x_value=0):
        """!
        @brief Enumerate compiled mana/life payment alternatives.

        Ordinary colored and generic symbols contribute directly to one fixed
        mana requirement. Hybrid-generic and Phyrexian symbols introduce
        alternative requirement/life combinations, which are expanded by a
        Cartesian-product search without touching game state.

        @param x_value Value chosen for variable symbols.
        @return Generator yielding `(requirement, life_payment)` pairs.
        @throws ValueError If X is invalid or a generalized symbol reaches this
            stage without being compiled to a subability.
        """
        if not isinstance(x_value, int) or isinstance(x_value, bool) or x_value < 0:
            raise ValueError("X must be a nonnegative integer.")

        fixed = ManaRequirement()
        alternatives = []

        for symbol, count in self.items():
            if isinstance(symbol, (DeterministicSymbol, ColoredSymbol)):
                fixed.add_pair(symbol.allowed, count)

            elif isinstance(symbol, GenericSymbol):
                fixed.add_pair(ALL_MANA, count)

            elif isinstance(symbol, VariableSymbol):
                fixed.add_pair(ALL_MANA, count * x_value)

            elif isinstance(symbol, HybridGenericSymbol):
                alternatives.append(
                    tuple(
                        (
                            {
                                frozenset({symbol.mana}): count - generic,
                                ALL_MANA: 2 * generic,
                            },
                            0,
                        )
                        for generic in range(count + 1)
                    )
                )

            elif isinstance(symbol, PhyrexianSymbol):
                alternatives.append(
                    tuple(
                        ({symbol.allowed: count - paid_life}, 2 * paid_life)
                        for paid_life in range(count + 1)
                    )
                )

            else:
                raise ValueError("Generalised symbols must be compiled as subabilities.")

        seen = set()

        def expand(index, requirement, life):
            """!
            @brief Recursively expand all alternative symbol-payment combinations.
            """
            if index == len(alternatives):
                option = (requirement.to_immutable(), life)

                if option not in seen:
                    seen.add(option)
                    yield option

                return

            for mana, life_cost in alternatives[index]:
                combined = ManaRequirement(requirement)
                combined.add(mana)
                yield from expand(index + 1, combined, life + life_cost)

        yield from expand(0, fixed, 0)

    def to_subability_defs(self):
        """!
        @brief Compile this mana value into executable subability definitions.

        Generalized symbols contribute their own supplied subdefinitions.
        Ordinary deterministic mana costs can become a direct `ManaActionNode`;
        costs with X, life alternatives or multiple payment forms remain as a
        mana-cost-bearing subability for later generation.

        @return Tuple of generated subability definitions.
        """
        from ..game_actions.data_structs.ability import SubAbilityDefinition
        from ..game_actions.data_structs.action_node import ManaActionNode

        ordinary = ManaValue(
            {
                symbol: count
                for symbol, count in self.items()
                if not isinstance(symbol, GeneralisedSymbol)
            }
        )

        subdefs = [
            symbol.subdef
            for symbol, count in self.items()
            if isinstance(symbol, GeneralisedSymbol)
            for _ in range(count)
        ]

        if ordinary:
            options = list(ordinary.payment_options())

            if len(options) != 1 or options[0][1] or ordinary.has_x:
                subdefs.append(SubAbilityDefinition(mana_cost=ordinary.to_immutable()))
            else:
                subdefs.append(SubAbilityDefinition(action_node=ManaActionNode(options[0][0])))

        return tuple(subdefs)

    @classmethod
    def parse(cls, text):
        """!
        @brief Parse a textual mana cost into an immutable mana value.
        """
        return parse_mana_cost(text)


class ManaValue(MutablePositiveCounterMapping[ManaSymbol], ManaValueBase):
    """!
    @brief Mutable mana-cost representation.
    """

    def __init__(self, values=None, generic=0, *, symbols=None):
        if isinstance(values, str):
            values = parse_mana_cost(values)

        elif values is not None and not isinstance(values, Mapping):
            symbols, values = values, None

        super().__init__(_positive_counts(values))
        _positive_counts({GenericSymbol(): generic})
        self.add_pair(GenericSymbol(), generic)

        for symbol in symbols or ():
            self.add_pair(symbol, 1)

    def to_immutable(self):
        """!
        @brief Return an immutable representation of this mana value.
        """
        return ImmutableManaValue(self)

    def to_mutable(self):
        """!
        @brief Return a mutable copy of this mana value.
        """
        return ManaValue(self)


class ImmutableManaValue(immutabledict[ManaSymbol, int], ManaValueBase):
    """!
    @brief Immutable mana-cost representation.
    """

    def __new__(cls, values=None):
        if isinstance(values, str):
            values = parse_mana_cost(values)
        return super().__new__(cls, _positive_counts(values))

    def to_mutable(self):
        """!
        @brief Return a mutable copy of this mana value.
        """
        return ManaValue(self)

    def to_immutable(self):
        """!
        @brief Return this immutable mana value unchanged.
        """
        return self


def parse_mana_cost(text):
    """!
    @brief Parse common textual mana-cost notation.

    Supports forms such as `{2}{W}{U/B}{2/R}{G/P}{X}` and compact ordinary
    costs such as `2WU`.

    Snow provenance, restricted mana and nonmana alternate costs require
    dedicated rule objects and are deliberately rejected rather than treated as
    generic mana.

    @param text String notation or an existing mana value.
    @return Immutable parsed mana value.
    @throws TypeError If the input is neither a string nor mana value.
    @throws ValueError If the notation or symbol form is unsupported.
    """
    if isinstance(text, ManaValueBase):
        return ImmutableManaValue(text)

    if not isinstance(text, str):
        raise TypeError("A mana cost must be a string or ManaValue.")

    text = "".join(text.upper().split())

    if "{" in text or "}" in text:
        tokens = re.findall(r"\{([^{}]+)\}", text)
        if "".join("{" + token + "}" for token in tokens) != text:
            raise ValueError("Invalid mana cost notation.")
    else:
        tokens = re.findall(r"\d+|[WUBRGCX]", text)
        if "".join(tokens) != text:
            raise ValueError("Invalid mana cost notation.")

    result = ManaValue()

    for token in tokens:
        if token.isdigit():
            result.add_pair(GenericSymbol(), int(token))

        elif token == "X":
            result.add_pair(VariableSymbol(), 1)

        elif token in MANA_LETTERS:
            result.add_pair(ColoredSymbol(frozenset({MANA_LETTERS[token]})), 1)

        else:
            parts = token.split("/")
            colors = [MANA_LETTERS[part] for part in parts if part in MANA_LETTERS]

            if (
                len(parts) == 2
                and parts[0] == "2"
                and len(colors) == 1
                and colors[0] != ManaType.COLORLESS
            ):
                symbol = HybridGenericSymbol(colors[0])

            elif (
                parts[-1] == "P"
                and len(parts) in {2, 3}
                and len(colors) == len(parts) - 1
                and ManaType.COLORLESS not in colors
            ):
                symbol = PhyrexianSymbol(frozenset(colors))

            elif (
                len(parts) == 2
                and len(colors) == 2
                and len(set(colors)) == 2
                and ManaType.COLORLESS not in colors
            ):
                symbol = ColoredSymbol(frozenset(colors))

            else:
                raise ValueError(f"Unsupported mana symbol {{{token}}}.")

            result.add_pair(symbol, 1)

    return result.to_immutable()


def generate_mana_options_from_req_pool(mana_req, mana_pool):
    """!
    @brief Enumerate distinct exact payments satisfying a compiled requirement.

    Requirement groups with fewer legal mana types are processed first so
    restrictive colored requirements consume suitable mana before flexible
    generic requirements. The search mutates temporary `free` and `payment`
    dictionaries in place and explicitly backtracks after every allocation.

    @param mana_req Compiled mana requirement.
    @param mana_pool Available mana pool.
    @return Generator yielding immutable exact payment mappings.
    """
    requirements = sorted(
        mana_req.items(),
        key=lambda pair: (
            len(pair[0]),
            tuple(sorted(m.value for m in pair[0])),
        ),
    )

    # Exact payment is impossible if the pool does not even contain enough
    # total mana, regardless of color restrictions.
    if sum(mana_req.values()) > mana_pool.amount():
        return

    free = dict(mana_pool)
    payment = {}
    seen = set()

    def allocate(index):
        """!
        @brief Allocate each requirement group from the currently free mana.
        """
        if index == len(requirements):
            result = ImmutableManaPool(payment)

            if result not in seen:
                seen.add(result)
                yield result

            return

        allowed, amount = requirements[index]
        colors = sorted(allowed, key=lambda mana: mana.value)

        def split(color_index, remaining):
            """!
            @brief Enumerate distributions of one requirement across allowed colors.
            """
            if remaining == 0:
                yield from allocate(index + 1)
                return

            # Prune when the remaining allowed colors cannot provide enough mana.
            if (
                color_index == len(colors)
                or sum(free.get(mana, 0) for mana in colors[color_index:]) < remaining
            ):
                return

            mana = colors[color_index]

            # Prefer consuming more of the current color first. Search still
            # enumerates all valid distributions, so this only affects order.
            for count in range(min(free.get(mana, 0), remaining), -1, -1):
                previous_payment = payment.get(mana, 0)

                free[mana] = free.get(mana, 0) - count
                payment[mana] = previous_payment + count

                yield from split(color_index + 1, remaining - count)

                # Restore both working structures before exploring the next split.
                free[mana] += count

                if previous_payment:
                    payment[mana] = previous_payment
                else:
                    payment.pop(mana, None)

        yield from split(0, amount)

    yield from allocate(0)