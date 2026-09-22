"""Printed card definitions and mutable runtime card identities."""

from __future__ import annotations
from typing import TYPE_CHECKING, override
from dataclasses import dataclass, field, replace
from helper.change_tracking import TrackedDict, TrackedSet
from collections.abc import Mapping
from uuid import uuid4
from immutabledict import immutabledict
from ..mana.mana_value import ImmutableManaValue

if TYPE_CHECKING:
    from .player import Player
    from .state import State
    from ..game_actions.data_structs.ability import (
        AbilityDefinition,
        AbilityCollection,
        TriggerAbilityDefinition,
        ManaValue,
    )
    from ..game_actions import GameEvent

from helper.runtime_object import KeyedCollection, RuntimeObject, ImmutableKeyedCollection
from .modifier import (
    ModifierSource,
    ContinuouosEffectModifierSource,
    CounterModifierSource,
    AttachedModifierSource,
    TimeStamp,
    Attachable,
    Modifier,
)
from .stat import Stat, HasModifiableStats, ModifiablePrimitiveStat, ModifiableReferenceStat
from ..enums import *
from ..stat_type import *
from helper.mutability_objs import *

__all__ = ["CardType", "CardSubtype", "ManaType", "CardDefinition", "Card"]


PERMANENT_TYPES: frozenset[CardType] = frozenset(
    {
        CardType.CREATURE,
        CardType.ARTIFACT,
        CardType.ENCHANTMENT,
        CardType.LAND,
        CardType.PLANESWALKER,
    }
)


class ZoneInfo:
    """!
    @brief Mutable zone metadata belonging to one runtime card.

    @var zone
        Zone in which the card currently exists.
    @var entered_at
        Timestamp associated with its latest entry into the current zone.
    """

    def __init__(self) -> None:
        self.zone: ZoneType = ZoneType.DECK
        self.entered_at: TimeStamp = TimeStamp(0, TurnPhase.UNTAP)


class AttachInfo:
    """!
    @brief Mutable attachment relation for one runtime card.

    @var attached_to
        Permanent currently hosting this attachment, if any.
    @var attached_at
        Timestamp of the latest attachment.
    """

    def __init__(self) -> None:
        self.attached_to: Card | None = None
        self.attached_at: TimeStamp | None = None


@dataclass(frozen=True)
class CardDefinition:
    """!
    @brief Immutable printed card data shared by all runtime copies.

    Rules-facing runtime state such as zone, damage, counters and controller
    lives on `Card`; this object describes the card's printed/base definition.
    """

    name: str

    # A prototype omitted cost means {0}; explicit None represents no mana cost.
    mana_cost: ManaValue | str | None = field(default_factory=ImmutableManaValue)
    color_identity: set[ManaType] = field(default_factory=set)

    types: frozenset[CardType] = field(default_factory=frozenset)
    subtypes: frozenset[CardSubtype] = field(default_factory=frozenset)

    triggers: frozenset[TriggerAbilityDefinition] = field(default_factory=frozenset)
    abilities: frozenset[AbilityDefinition] = field(default_factory=frozenset)

    power: int | None = None
    toughness: int | None = None

    attach_mods: immutabledict[StatType, tuple[Modifier, ...]] | None = None
    continuous_effects: tuple = ()
    keywords: frozenset[str] = field(default_factory=frozenset)
    enters_tapped: bool = False
    legendary: bool = False
    loyalty: int | None = None

    # Optional enchant restriction: (state, aura, host) -> bool.
    enchant: object | None = None

    replacement_effects: tuple = ()
    colors: frozenset[ManaType] = field(default_factory=frozenset)

    # Human-readable Oracle text used by the starter catalog and UI. Rules
    # remain executable objects in ``abilities``/``triggers``; this field is
    # intentionally descriptive and does not replace them.
    oracle_text: str = ""

    def __post_init__(self):
        """!
        @brief Validate and normalize immutable printed card data.

        Keyword spelling is normalized for runtime lookup and string mana costs
        are parsed into the engine's `ManaValue` representation.

        @throws ValueError If starting loyalty is malformed.
        """
        if self.loyalty is not None and (
            type(self.loyalty) is not int or self.loyalty < 0
        ):
            raise ValueError("Starting loyalty must be a nonnegative integer.")

        object.__setattr__(
            self,
            "keywords",
            frozenset(
                keyword.lower().replace("_", " ").replace("-", " ")
                for keyword in self.keywords
            ),
        )

        if isinstance(self.mana_cost, str):
            from ..mana.mana_value import ManaValue

            object.__setattr__(
                self,
                "mana_cost",
                ManaValue.parse(self.mana_cost),
            )

    def is_permanent(self) -> bool:
        """!
        @brief Return whether this definition represents a permanent card.

        @return `False` for instants and sorceries, otherwise `True`.
        """
        return (
            CardType.INSTANT not in self.types
            and CardType.SORCERY not in self.types
        )


@dataclass(frozen=True)
class CardData:
    """!
    @brief Small immutable serializable/card-display data subset.
    """

    name: str
    color_identity: set[ManaType]


class CardRuntimeState:
    """!
    @brief Mutable per-card state that changes during gameplay.

    Tracked containers call `on_change` whenever their content changes so the
    owning card can invalidate derived query indexes and continuous effects.
    """

    def __init__(
        self,
        tapped: bool | None = None,
        counters: dict[CounterType, int] | None = None,
        active_cont_effects: set[str] | None = None,
        attach_info: AttachInfo | None = None,
        attached: dict[str, Attachable] | None = None,
        damage_marked: int | None = None,
        face_down: bool = False,
        on_change=lambda: None,
    ) -> None:
        """!
        @brief Initialize mutable gameplay state for one card.

        @param on_change Callback invoked by tracked mutable fields.
        """
        self._on_change = on_change
        self.zone_info: ZoneInfo = ZoneInfo()
        self._tapped = tapped

        self.counters = TrackedDict(
            counters or {},
            on_change,
        )
        self.active_cont_effects = TrackedSet(
            active_cont_effects or (),
            on_change,
        )

        self.attach_info: AttachInfo = (
            attach_info
            if attach_info is not None
            else AttachInfo()
        )

        self.attached = TrackedDict(
            attached or {},
            on_change,
        )

        self._damage_marked: int = (
            damage_marked
            if damage_marked is not None
            else 0
        )

        self.face_down: bool = face_down
        self._damage_by_deathtouch = False
        self._skip_untap = None
        self.mana_x = 0

        self._anchored_modifiers: list[str] = []

    @property
    def skip_untap(self):
        """Optional (zone revision, player) marker, consumed at that player's untap."""
        return self._skip_untap

    @skip_untap.setter
    def skip_untap(self, value):
        self._skip_untap = value
        self._on_change()

    @property
    def damage_marked(self):
        return self._damage_marked

    @damage_marked.setter
    def damage_marked(self, value):
        self._damage_marked = value
        self._on_change()

    @property
    def damage_by_deathtouch(self):
        return self._damage_by_deathtouch

    @damage_by_deathtouch.setter
    def damage_by_deathtouch(self, value):
        self._damage_by_deathtouch = value
        self._on_change()

    @property
    def tapped(self):
        """!
        @brief Return the stored tapped state.
        """
        return self._tapped

    @tapped.setter
    def tapped(self, value):
        """!
        @brief Change tapped state and invalidate derived state.
        """
        self._tapped = value
        self._on_change()

    @classmethod
    def create_permanent_card_state(
        cls,
        tapped: bool = False,
        counters: dict[CounterType, int] | None = None,
        attach_info: AttachInfo | None = None,
        damage_marked: int = 0,
        on_change=lambda: None,
    ) -> CardRuntimeState:
        """!
        @brief Create runtime state initialized for a permanent card.

        Permanent cards use a concrete boolean tapped state from creation.

        @return New `CardRuntimeState`.
        """
        return cls(
            tapped=tapped,
            counters=counters,
            attach_info=attach_info,
            damage_marked=damage_marked,
            on_change=on_change,
        )


class Card(HasModifiableStats, Attachable):
    """!
    @brief Mutable runtime identity representing one physical/game card.

    A `Card` keeps a stable runtime identity while its zone, controller,
    counters, attachments and derived characteristics change. Printed values
    originate in `CardDefinition` and are exposed through modifiable stats.
    """

    def __init__(
        self,
        prototype: CardDefinition,
        owner: Player,
        key: str | None = None,
        *,
        is_token: bool = False,
    ) -> None:
        """!
        @brief Create a runtime card from an immutable card definition.

        @param prototype Static printed card definition.
        @param owner Player who permanently owns the card.
        @param key Optional stable runtime key.
        @param is_token Whether this runtime object represents a token.
        @throws ValueError If the supplied key is not a valid card key.
        """
        RuntimeObject.__init__(self)

        self._key: str = key if key is not None else uuid4().hex

        if (
            not self._key
            or "." in self._key
            or self._key != self._key.lower()
        ):
            raise ValueError(
                "Card keys must be nonempty lowercase strings without dots."
            )

        self._definition: CardDefinition = prototype

        self._owner = owner
        self._game_state = None
        self.is_token = is_token
        self.token_left_battlefield = False
        self.loyalty_activated_turn = None

        # Incremented whenever the card changes zones. References recorded
        # against an earlier incarnation can therefore detect leave/re-entry.
        self.zone_revision = 0

        # Turn in which this incarnation last entered or changed controller.
        self.controlled_since = -1

        self._state = (
            CardRuntimeState.create_permanent_card_state(
                on_change=self._notify_changed
            )
            if prototype.is_permanent()
            else CardRuntimeState(on_change=self._notify_changed)
        )

        # Base stats are initialized from the printed definition. Continuous
        # effects, counters and attachments are layered later through modifier
        # sources rather than mutating these printed/base values directly.
        self._stats: immutabledict[StatType, Stat] = immutabledict(
            {
                STAT_MANA_COST: ModifiableReferenceStat(
                    prototype.mana_cost,
                    STAT_MANA_COST,
                ),
                STAT_GENERIC_COST_REDUCTION: ModifiablePrimitiveStat(
                    0,
                    STAT_GENERIC_COST_REDUCTION,
                ),
                STAT_STATIC_ABILITIES: ModifiableReferenceStat(
                    ImmutableSet(
                        list(
                            rule.key
                            for rule in (
                                *prototype.continuous_effects,
                                *prototype.replacement_effects,
                            )
                        )
                        + (
                            ["builtin:enter_tapped"]
                            if prototype.enters_tapped
                            else []
                        )
                    ),
                    STAT_STATIC_ABILITIES,
                ),
                STAT_INTRINSIC_MANA: ModifiablePrimitiveStat(
                    True,
                    STAT_INTRINSIC_MANA,
                ),
                STAT_KEYWORDS: ModifiableReferenceStat(
                    ImmutableSet(prototype.keywords),
                    STAT_KEYWORDS,
                ),
                STAT_COLORS: ModifiableReferenceStat(
                    ImmutableSet(prototype.colors),
                    STAT_COLORS,
                ),
                STAT_TYPES: ModifiableReferenceStat(
                    ImmutableSet(prototype.types),
                    STAT_TYPES,
                ),
                STAT_SUBTYPES: ModifiableReferenceStat(
                    ImmutableSet(prototype.subtypes),
                    STAT_SUBTYPES,
                ),
                STAT_TRIGGERS: ModifiableReferenceStat(
                    self._keyed_definitions(
                        prototype.triggers,
                        "trigger",
                    ),
                    STAT_TRIGGERS,
                ),
                STAT_ABILITIES: ModifiableReferenceStat(
                    self._keyed_definitions(
                        prototype.abilities,
                        "ability",
                    ),
                    STAT_ABILITIES,
                ),
                STAT_POWER: ModifiablePrimitiveStat(
                    prototype.power,
                    STAT_POWER,
                ),
                STAT_TOUGHNESS: ModifiablePrimitiveStat(
                    prototype.toughness,
                    STAT_TOUGHNESS,
                ),
                STAT_CONTROLLER: ModifiablePrimitiveStat(
                    owner,
                    STAT_CONTROLLER,
                ),
                STAT_ATTACH_MODS: ModifiableReferenceStat(
                    ImmutableDict(prototype.attach_mods or {}),
                    STAT_ATTACH_MODS,
                ),
            }
        )

        self._modifier_sources: tuple[ModifierSource] = (
            ContinuouosEffectModifierSource(
                self._state.active_cont_effects
            ),
            CounterModifierSource(
                self._state.counters
            ),
            AttachedModifierSource(
                self._state.attached
            ),
        )

    @staticmethod
    def _keyed_definitions(definitions, prefix):
        """!
        @brief Normalize ability/trigger definitions into a keyed immutable collection.

        Definitions that already arrive as a mapping preserve their keys.
        Sequence entries without explicit keys receive deterministic generated
        keys such as `ability1` or `trigger1`.

        @param definitions Mapping or iterable of definitions.
        @param prefix Prefix used for generated keys.
        @return Immutable keyed collection.
        @throws ValueError If two definitions resolve to the same key.
        """
        if isinstance(definitions, Mapping):
            return ImmutableKeyedCollection(definitions)

        result = {}

        for index, definition in enumerate(definitions, 1):
            key = getattr(definition, "key", None) or f"{prefix}{index}"

            if key in result:
                raise ValueError(f"Duplicate definition key: {key}")

            result[key] = definition

        return ImmutableKeyedCollection(result)

    # The dataclass mixins have no identity fields; cards use their runtime key.
    __eq__ = RuntimeObject.__eq__
    __hash__ = RuntimeObject.__hash__

    # --------------------------------------------------------------
    # --------------------------------------------------------------
    # Getters for private attributtes
    # --------------------------------------------------------------
    # --------------------------------------------------------------

    @property
    def definition(self) -> CardDefinition:
        """!
        @brief Return the card's current base definition.

        Entry-copy effects may temporarily replace this definition for one
        battlefield incarnation.
        """
        return self._definition

    @property
    def owner(self) -> Player:
        """!
        @brief Return the permanent owner of this card.
        """
        return self._owner

    @property
    def name(self) -> str:
        """!
        @brief Return the current definition's printed name.
        """
        return self.definition.name

    @property
    def card_def(self) -> CardDefinition:
        """!
        @brief Compatibility alias for `definition`.
        """
        return self.definition

    @property
    def state(self) -> CardRuntimeState:
        """!
        @brief Return mutable runtime state belonging to this card.
        """
        return self._state

    @property
    def zone(self) -> ZoneType:
        """!
        @brief Return the card's current zone.
        """
        return self._state.zone_info.zone

    def get_zone(self) -> ZoneType:
        """!
        @brief Return the card's current zone.
        """
        return self.zone

    def set_zone(
        self,
        zone: ZoneType,
        time_stamp: TimeStamp | None = None,
    ) -> None:
        """!
        @brief Update zone metadata and reset incarnation-local runtime state.

        This method updates card-local metadata only; moving the card between
        actual player/state zone collections is the responsibility of
        `Player.move_card`.

        A real zone change creates a new card incarnation by incrementing
        `zone_revision`, breaking attachments, clearing counters/damage and
        restoring ownership-based control.

        @param zone New zone.
        @param time_stamp Optional timestamp for entry into that zone.
        """
        changed = zone != self.get_zone()
        current_state = self._game_state or self.owner.game_state
        if changed and current_state is not None:
            from ..game_actions.resolution.event_bus import capture_single_card

            # Preserve departure-time values, not activation-time values.
            # Historical revisions remain usable by independently pending abilities.
            history = getattr(self, "_incarnation_history", None)
            if history is None:
                history = self._incarnation_history = {}
            history[self.zone_revision] = capture_single_card(current_state, self)

        # An enters-as-copy replacement may temporarily replace the definition
        # and stat table. Leaving that incarnation restores the original pair.
        if changed and getattr(self, "_entry_copy_restore", None) is not None:
            self._definition, self._stats = self._entry_copy_restore
            self._entry_copy_restore = None

        if changed:
            if self.is_token and self.get_zone() == ZoneType.BATTLEFIELD:
                self.token_left_battlefield = True

            self.loyalty_activated_turn = None

            # Attachments do not survive the card changing zones. Both the card
            # as an attachment and objects attached to it are disconnected.
            self.detach()

            for attachment in tuple(self.state.attached.values()):
                attachment.detach()

        if zone != self.get_zone():
            self.zone_revision += 1

        if self._state.zone_info.zone != zone:
            # These properties belong to the old zone incarnation and therefore
            # reset before the new incarnation becomes visible.
            self._state.tapped = False
            self._state.counters.clear()
            self._state.damage_marked = 0
            self._state.damage_by_deathtouch = False
            self._state.mana_x = 0

            current_state = self._game_state or self.owner.game_state

            if current_state is not None:
                self.controlled_since = current_state.turn.number

                if hasattr(current_state, "combat"):
                    current_state.combat.remove(self)

            self.set_controller(self.owner)

        self._state.zone_info.zone = zone

        # Starting loyalty and enters-tapped are properties of the new
        # battlefield incarnation, not persistent state from the old zone.
        if (
            changed
            and zone == ZoneType.BATTLEFIELD
            and self.definition.loyalty is not None
        ):
            self.state.counters[CounterType.LOYALTY] = self.definition.loyalty

        if (
            changed
            and zone == ZoneType.BATTLEFIELD
            and self.definition.enters_tapped
        ):
            self._state.tapped = True

        if time_stamp is not None:
            self._state.zone_info.entered_at = time_stamp

        self._notify_changed()

    @property
    def is_token(self):
        return self._is_token

    @is_token.setter
    def is_token(self, value):
        self._is_token = value
        self._notify_changed()

    def _notify_changed(self) -> None:
        """!
        @brief Notify the owning game state that indexed card data may be stale.
        """
        if self._game_state is not None:
            self._game_state.notify_card_changed(self)

    def set_base_stat(
        self,
        stat_type: StatType,
        value,
    ) -> None:
        """!
        @brief Replace one runtime base stat and invalidate derived indexes.

        Controller changes also reset the card's continuous-control timestamp
        and remove it from current combat.

        @param stat_type Stat whose base value is being replaced.
        @param value New base value.
        @throws ValueError If a controller does not belong to this game.
        """
        if (
            stat_type == STAT_CONTROLLER
            and self._game_state is not None
            and value not in self._game_state.players
        ):
            raise ValueError("Controller must belong to this game.")

        if (
            stat_type == STAT_CONTROLLER
            and self._game_state is not None
            and value is not self.get_controller(self._game_state)
        ):
            self.controlled_since = self._game_state.turn.number

            if hasattr(self._game_state, "combat"):
                self._game_state.combat.remove(self)

        # Collection-valued stats use immutable wrappers so modifier evaluation
        # and register snapshots cannot observe accidental in-place mutation.
        if stat_type in (
            STAT_TYPES,
            STAT_SUBTYPES,
            STAT_KEYWORDS,
            STAT_COLORS,
            STAT_STATIC_ABILITIES,
        ):
            value = ImmutableSet(value)

        stat = self._stats[stat_type]

        self._stats = immutabledict(
            {
                **self._stats,
                stat_type: replace(
                    stat,
                    base_value=value,
                ),
            }
        )

        self._notify_changed()

    def set_controller(self, player: Player) -> None:
        """!
        @brief Set the card's base controller.
        """
        self.set_base_stat(
            STAT_CONTROLLER,
            player,
        )

    @property
    def is_tapped(self) -> bool:
        """!
        @brief Return whether the card is currently tapped.
        """
        return bool(self._state.tapped)

    @is_tapped.setter
    def is_tapped(self, value: bool) -> None:
        """!
        @brief Set tapped state and notify change tracking.
        """
        self._state.tapped = value

    def get_controller(self, state: State) -> Player:
        """!
        @brief Return the card's controller after applicable modifiers.
        """
        return self.get_stat(
            STAT_CONTROLLER,
            state,
        )

    # --------------------------------------------------------------
    # RuntimeObject properties
    # --------------------------------------------------------------

    @property
    @override
    def key(self):
        """!
        @brief Return this card's stable runtime key.
        """
        return self._key

    # --------------------------------------------------------------
    # HasStats properties
    # --------------------------------------------------------------

    @property
    @override
    def stats(self):
        """!
        @brief Return the card's modifiable stat table.
        """
        return self._stats

    # --------------------------------------------------------------
    # HasModifiers properties
    # --------------------------------------------------------------

    @property
    @override
    def modifier_sources(self):
        """!
        @brief Return modifier sources contributing to derived characteristics.
        """
        return self._modifier_sources

    # --------------------------------------------------------------
    # Attachable properties
    # --------------------------------------------------------------

    @property
    @override
    def attached_to(self):
        """!
        @brief Return the permanent currently hosting this attachment.
        """
        return self._state.attach_info.attached_to

    @attached_to.setter
    @override
    def attached_to(self, value: Card | None):
        self._state.attach_info.attached_to = value

    @property
    @override
    def last_attach_at(self) -> TimeStamp | None:
        """!
        @brief Return the timestamp of the latest attachment.
        """
        return self._state.attach_info.attached_at

    @last_attach_at.setter
    @override
    def last_attach_at(self, value: TimeStamp | None) -> None:
        self._state.attach_info.attached_at = value

    # --------------------------------------------------------------
    # --------------------------------------------------------------
    # Methods
    # --------------------------------------------------------------
    # --------------------------------------------------------------

    # --------------------------------------------------------------
    # Attachable override methdods
    # --------------------------------------------------------------

    @override
    def modifiers_to_attach(self, state):
        """!
        @brief Return modifiers granted to the object hosting this attachment.
        """
        return self.get_stat(
            STAT_ATTACH_MODS,
            state,
        )

    # --------------------------------------------------------------
    # Own methods
    # --------------------------------------------------------------

    def __str__(self) -> str:
        """!
        @brief Return a human-readable name and runtime key.
        """
        return f"{self.name} [{self.key}]"

    # ManaValue methods
    def get_mana_cost(self, state: State) -> ManaValue:
        """!
        @brief Return the current mana-cost characteristic.
        """
        return self.get_stat(
            STAT_MANA_COST,
            state,
        )

    def get_casting_cost(self, state: State):
        """!
        @brief Return the payable casting cost after generic reductions.

        Casting reductions affect the payable cost only; they do not mutate the
        card's mana-cost characteristic or mana value.

        @param state Current game state.
        @return Reduced immutable mana value, or `None` for no mana cost.
        """
        from ..mana.mana_value import ManaValue, GenericSymbol

        state.refresh_continuous_effects()

        cost = self.get_mana_cost(state)

        if cost is None:
            return None

        reduced = ManaValue(cost)

        amount = min(
            reduced.generic,
            self.get_stat(
                STAT_GENERIC_COST_REDUCTION,
                state,
            ),
        )

        if amount > 0:
            reduced.substract_pair(
                GenericSymbol(),
                amount,
            )

        return reduced.to_immutable()

    def get_mana_value(self, state):
        """!
        @brief Return the card's current mana value.

        X contributes its chosen runtime value only while the card is on the
        stack; in other zones X contributes zero.
        """
        cost = self.get_mana_cost(state)

        return (
            cost.cmc(
                self.state.mana_x
                if self.get_zone() == ZoneType.STACK
                else 0
            )
            if cost is not None
            else 0
        )

    # Types methods
    def get_types(self, state: State) -> frozenset[CardType]:
        """!
        @brief Return current card types after modifiers.
        """
        return self.get_stat(
            STAT_TYPES,
            state,
        )

    def is_type(
        self,
        state: State,
        type: CardType,
    ) -> bool:
        """!
        @brief Test whether the card currently has one card type.
        """
        return type in self.get_types(state)

    def get_subtypes(self, state: State) -> frozenset[CardSubtype]:
        """!
        @brief Return current card subtypes after modifiers.
        """
        return self.get_stat(
            STAT_SUBTYPES,
            state,
        )

    def is_subtype(
        self,
        state: State,
        subtype: CardSubtype,
    ) -> bool:
        """!
        @brief Test whether the card currently has one subtype.
        """
        return subtype in self.get_subtypes(state)

    # Abilities methods
    def get_ability_defs(self, state: State) -> AbilityCollection:
        """!
        @brief Return the card's current printed, granted and intrinsic abilities.

        Basic-land mana abilities are generated dynamically when intrinsic mana
        abilities remain enabled.
        """
        from game.rules.lands import basic_land_abilities

        definitions = self.get_stat(
            STAT_ABILITIES,
            state,
        )

        intrinsic = (
            basic_land_abilities(self, state)
            if self.get_stat(STAT_INTRINSIC_MANA, state)
            else {}
        )

        # Playing a land is represented explicitly, independently of intrinsic mana.
        if CardType.LAND in self.get_types(state):
            from game.rules.lands import PLAY_LAND_DEFINITION
            if PLAY_LAND_DEFINITION.key not in definitions:
                intrinsic = {PLAY_LAND_DEFINITION.key: PLAY_LAND_DEFINITION, **intrinsic}

        if not intrinsic:
            return definitions

        return ImmutableKeyedCollection(
            {
                **definitions,
                **intrinsic,
            }
        )

    def get_activatable_ability_defs(
        self,
        state: State,
    ) -> AbilityCollection:
        """!
        @brief Collect abilities currently available from this card's zone.

        @param state Current game state.
        @return Ability definitions legal to activate from the current zone.
        """
        from ..game_actions.data_structs.ability import AbilityCollection, PlayLandAbilityDefinition

        return AbilityCollection(
            {
                k: v
                for k, v in self.get_ability_defs(state).items()
                if v.is_usable_in_zone(self.zone) and not isinstance(v, PlayLandAbilityDefinition)
            }
        )

    def get_land_play_ability_defs(self, state):
        """Return intrinsic and effect-granted land-play permissions."""
        from ..game_actions.data_structs.ability import PlayLandAbilityDefinition
        return {key: definition for key, definition in self.get_ability_defs(state).items()
                if isinstance(definition, PlayLandAbilityDefinition)}

    def get_ability_def(
        self,
        key: str,
        state: State,
    ) -> AbilityDefinition | None:
        """!
        @brief Find one current ability definition by key.
        """
        return self.get_ability_defs(state).get(key)

    def try_find_ability(
        self,
        key: str,
        state: State | None = None,
    ):
        """!
        @brief Construct a runtime ability when the requested definition is usable.

        With a game state, current modified ability definitions and controller
        are used. Without one, lookup falls back to base ability definitions and
        the card's owner.

        @return Runtime `Ability`, or `None` if absent/unusable.
        """
        definitions = (
            self.get_ability_defs(state)
            if state is not None
            else self.get_base_value(STAT_ABILITIES)
        )

        definition = definitions.get(key)

        if (
            definition is None
            or not definition.is_usable_in_zone(self.zone)
        ):
            return None

        controller = (
            self.get_controller(state)
            if state is not None
            else self.owner
        )

        return definition.to_ability(self, controller)

    # Triggers methods
    def get_trigger_defs(
        self,
        state: State,
    ) -> KeyedCollection[TriggerAbilityDefinition]:
        """!
        @brief Return current trigger definitions after modifiers.
        """
        return self.get_stat(
            STAT_TRIGGERS,
            state,
        )

    def get_triggers_with_event(
        self,
        event: GameEvent,
        state: State,
    ) -> KeyedCollection[TriggerAbilityDefinition]:
        """!
        @brief Return trigger definitions matching one event in the current state.

        Zone legality is checked before the trigger condition itself.

        @param event Event being inspected.
        @param state Current game state.
        @return Matching trigger definitions.
        """
        return KeyedCollection(
            {
                k: v
                for k, v in self.get_trigger_defs(state).items()
                if (
                    v.is_usable_in_zone(self.zone)
                    and v.condition.matches(state, event)
                )
            }
        )

    def get_trigger_def(
        self,
        key: str,
        state: State,
    ) -> TriggerAbilityDefinition | None:
        """!
        @brief Find one current trigger definition by key.
        """
        return self.get_trigger_defs(state).get(key)

    # Power and Tougness methods
    def get_power(
        self,
        state: State,
    ) -> int | None:
        """!
        @brief Return current power after refreshing continuous effects.
        """
        state.refresh_continuous_effects()

        return self.get_stat(
            STAT_POWER,
            state,
        )

    def get_toughness(
        self,
        state: State,
    ) -> int | None:
        """!
        @brief Return current toughness after refreshing continuous effects.
        """
        state.refresh_continuous_effects()

        return self.get_stat(
            STAT_TOUGHNESS,
            state,
        )

    def is_dead(
        self,
        state: State,
    ) -> bool:
        """!
        @brief Test current lethal/state-based creature death conditions.

        Zero or negative toughness kills regardless of indestructible. Lethal
        marked damage and deathtouch damage are ignored by indestructible.

        @return Whether the card currently satisfies these death conditions.
        """
        tougness = self.get_toughness(state)

        if tougness is None:
            return False

        return tougness <= 0 or (
            not self.has_keyword(state, "indestructible")
            and (
                self.state.damage_marked >= tougness
                or self.state.damage_by_deathtouch
            )
        )

    # Summoning sick
    def is_summoning_sick(
        self,
        state: State,
    ) -> bool:
        """!
        @brief Return whether the card is currently affected by summoning sickness.

        The check applies only to creatures without haste and compares the
        current control incarnation against the controller's latest turn start.
        """
        return (
            CardType.CREATURE in self.get_types(state)
            and not self.has_keyword(state, "haste")
            and self.controlled_since
            >= self.get_controller(state).last_turn_started
        )

    def has_keyword(
        self,
        state: State,
        name: str,
    ) -> bool:
        """!
        @brief Test for a normalized keyword in the current keyword set.

        Underscores and hyphens are normalized to spaces to match definition
        normalization performed by `CardDefinition`.
        """
        return (
            name.lower()
            .replace("_", " ")
            .replace("-", " ")
            in self.get_stat(
                STAT_KEYWORDS,
                state,
            )
        )