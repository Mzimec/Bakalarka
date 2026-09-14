"""Shared game state and coordination of card and player registries."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .player import Player
    from .card import Card

    from ..abilities import Ability
    from ..game_actions.data_structs.ability import TriggerAbility
    from ..game_actions.resolution.event_bus import GameEvent

from ..game_actions.game_stack import GameStack, PrioritySystem
from ..game_loop.game_loop import Turn
from .look_up import LookUpSystem, LookUpResult
from .modifier import ContinuousEffectsManager, ContinuousEffect, TimeStamp

from ..enums import *

__all__ = ["State"]


class State:
    """!
    @brief Central mutable game state and coordinator of runtime subsystems.

    Owns players, stack, priority, turn state, combat state, registries,
    continuous effects and other game-wide services.
    """

    def __init__(
        self, players: list[Player], active_player_idx: int = 0, *, skip_first_draw: bool = True
    ) -> None:
        """!
        @brief Create and bind a complete game state.

        Initial player and card identities are validated before runtime objects
        are bound to this state and registered in query indexes.

        @param players Players participating in the game.
        @param active_player_idx Index of the player who starts as active.
        @param skip_first_draw Whether the starting player's first draw is skipped.
        @throws ValueError If no players are supplied, the active-player index is
            invalid, players are reused, or initial card identities conflict.
        """
        self._players = tuple(players)

        if not self._players:
            raise ValueError("A game state needs at least one player.")

        if active_player_idx < 0 or active_player_idx >= len(self._players):
            raise ValueError("The active player index is out of range.")

        if len(set(self._players)) != len(self._players) or any(
            p.game_state is not None for p in self._players
        ):
            raise ValueError("Players must be distinct and cannot belong to another game.")

        # Player indices are canonicalized according to their position in this state.
        for idx, player in enumerate(self._players):
            player._idx = idx

        self._active_player_idx: int = active_player_idx
        self._stack: GameStack = GameStack()
        self._priority: PrioritySystem = PrioritySystem(self)
        self._turn = Turn()
        self.skip_first_draw = skip_first_draw
        self.cleanup_priority = False
        self._deferred_events = []

        self.active_player.last_turn_started = self._turn.number

        from game.rules.combat import CombatState

        self.combat = CombatState(self)
        self._lookup_system = LookUpSystem()
        self._cont_effect_manager = ContinuousEffectsManager()

        # These flags coordinate lazy/re-entrant continuous-effect refresh.
        self._refreshing_effects = False
        self._static_effect_keys = set()
        self._effects_dirty = True
        self._effects_checked_at = None

        self.replacement_rules = []

        from .registers.card_register import CardRegister
        from .registers.player_register import PlayerRegister

        self.card_register = CardRegister(self)
        self.player_register = PlayerRegister(self)

        # Validate all initial identities before binding anything to this state.
        cards = self.get_cards()
        if len({card.key for card in cards}) != len(cards) or any(
            card.runtime_id is not None for card in cards
        ):
            raise ValueError("Initial cards must have distinct, unregistered identities.")

        for player in self.players:
            player._game_state = self
            self.player_register.register(player)

        for card in cards:
            self.register_card(card)

    def register_card(self, card: Card) -> None:
        """!
        @brief Bind and register a card already present in its owner's zone.

        Cards must enter the state through their owner's zone-management API so
        that zone collections and runtime registries cannot diverge.

        @param card Card to register.
        @throws ValueError If the card is not present in its owner's collections.
        """
        if card.owner not in self.players or card.owner.try_find_card(card.key) is not card:
            raise ValueError("Register cards through their owner's add_card method.")

        self.card_register.register(card)
        card._game_state = self

    def notify_card_changed(self, card: Card) -> None:
        """!
        @brief Mark a card's derived query-index memberships as dirty.

        @param card Card whose effective characteristics may have changed.
        """
        self.card_register.mark_changed(card)

    def synchronise_registers(self) -> None:
        """!
        @brief Bring continuous effects and runtime query indexes up to date.

        Continuous effects are refreshed first because card index memberships
        may depend on characteristics derived from those effects.
        """
        self.refresh_continuous_effects()
        self.card_register.synchronise()
        self.player_register.synchronise()

    def refresh_continuous_effects(self):
        """!
        @brief Recompute continuous-effect memberships and layered characteristics.
        """
        from .continuous_rules import refresh_continuous_effects

        refresh_continuous_effects(self)

    def add_continuous_effect(self, effect):
        """!
        @brief Register a continuous effect and immediately synchronize derived state.

        @param effect Continuous effect to add.
        """
        self._cont_effect_manager.add(effect)
        self._effects_dirty = True
        self.synchronise_registers()

    def query_cards(self, query):
        """!
        @brief Evaluate a query against the synchronized card register.

        @param query Card query expression.
        @return Materialized tuple of matching cards.
        """
        return tuple(self.card_register.query(query))

    def query_players(self, query):
        """!
        @brief Evaluate a query against the synchronized player register.

        @param query Player query expression.
        @return Materialized tuple of matching players.
        """
        return tuple(self.player_register.query(query))

    @property
    def active_player(self) -> Player:
        return self._players[self._active_player_idx]

    @property
    def players(self) -> tuple[Player, ...]:
        return self._players

    @property
    def active_players(self) -> tuple[Player, ...]:
        """!
        @brief Return players that have not yet lost the game.
        """
        return tuple(player for player in self.players if player.is_alive)

    @property
    def active_player_idx(self) -> int:
        return self._active_player_idx

    @property
    def time_stamp(self) -> TimeStamp:
        """!
        @brief Return the current global turn/phase timestamp.
        """
        return TimeStamp(self.turn.number, self.turn.phase)

    @property
    def stack(self) -> GameStack:
        return self._stack

    @property
    def priority(self) -> PrioritySystem:
        return self._priority

    @property
    def turn(self) -> Turn:
        return self._turn

    def lookup(self, key: str) -> LookUpResult:
        """!
        @brief Resolve a hierarchical runtime-object lookup key.

        @param key Lookup key.
        @return Lookup result containing the deepest resolved object.
        """
        return self._lookup_system.lookup(key, self)

    def _cycle_player_idx(self, idx) -> int:
        """!
        @brief Wrap a player index across the bounds of the player tuple.
        """
        if idx >= len(self._players):
            idx = 0

        if idx < 0:
            idx = len(self._players) - 1

        return idx

    def get_next_player(self, player: Player) -> Player:
        """!
        @brief Return the next player in turn order.

        @param player Player whose successor should be found.
        @return Next player, wrapping around at the end.
        """
        return self._players[self._cycle_player_idx(self._players.index(player) + 1)]

    def switch_active_player(self) -> None:
        """!
        @brief Advance the active player and reset the priority system.
        """
        self._active_player_idx = self._cycle_player_idx(self._active_player_idx + 1)
        self._priority.reset()

    def begin_turn(self):
        """!
        @brief Initialize per-turn player state for the newly active turn.
        """
        self.active_player.last_turn_started = self.turn.number

        for player in self.players:
            player.lands_played_this_turn = 0

    @property
    def is_game_over(self):
        """!
        @brief Return whether at least one player has lost.
        """
        return any(not player.is_alive for player in self.players)

    def defer_events(self, events):
        """!
        @brief Append events to the state's deferred event queue.

        @param events Events to process later.
        """
        self._deferred_events.extend(events)

    def get_cards(
        self, from_players: list[Player] | None = None, from_zones: list[ZoneType] | None = None
    ) -> list[Card]:
        """!
        @brief Collect cards from selected players and zones.

        @param from_players Players to inspect, or all players when omitted.
        @param from_zones Zones to inspect, or all zones when omitted.
        @return Materialized list of known cards.
        """
        if from_players is None:
            from_players = self._players

        if from_zones is None:
            from_zones = [z for z in ZoneType]

        cards: list[Card] = []

        for player in from_players:
            cards.extend(player.get_cards(from_zones))

        return cards

    def get_mana_sources(self, player):
        """!
        @brief Discover deterministic mana-producing sources without paying costs.

        @param player Player whose mana sources should be inspected.
        @return Available mana-source descriptions.
        """
        from ..mana.source_discovery import mana_sources

        return mana_sources(self, player)

    def can_activate(self, card, ability_key):
        """!
        @brief Check whether a mana ability can currently be activated as a cost action.

        Validation covers battlefield presence, current ability definition,
        controller priority, ability-level validation and every cost effect.

        @param card Card containing the mana ability.
        @param ability_key Ability identifier.
        @return True if the ability is currently activatable.
        """
        if card not in self.get_cards(from_zones=[ZoneType.BATTLEFIELD]):
            return False

        definition = card.get_ability_def(ability_key, self)
        if definition is None or not definition.is_mana_ability:
            return False

        controller = card.get_controller(self)
        if self.priority.current_player is not controller:
            return False

        error = definition.validation_error(card, controller, self)
        if error:
            return False

        from ..game_actions.data_structs.game_action import ResolutionContext
        from dataclasses import replace

        # Cost validation uses the same runtime context shape as an actual
        # activation, but performs no operation and pays no cost.
        context = ResolutionContext(
            controller=controller,
            source=card,
            ability=definition,
            action_key=ability_key,
            is_cost=True,
        )

        for subdef in definition.cost_subdefs:
            for effect in subdef.effects:
                if effect.validation_error(self, replace(context, targets=None)):
                    return False

        return True

    def get_cont_effect(self, key: str) -> ContinuousEffect | None:
        """!
        @brief Return a live continuous effect by runtime key.

        @param key Continuous-effect key.
        @return Matching effect, or `None` if absent.
        """
        return self._cont_effect_manager.get(key)

    """
    TODO
    """

    def get_turn_phase(self) -> TurnPhase:
        """!
        @brief Return the current turn phase.
        """
        return self._turn.phase

    def get_granted_abilities(self, card: Card) -> list[Ability]:
        """!
        @brief Return abilities granted to a card by external effects.

        Current implementation provides no additional granted abilities.

        @param card Card whose granted abilities should be collected.
        @return Granted abilities.
        """
        return []

    def get_trigger_abilities(self, event: GameEvent | None = None) -> list[TriggerAbility]:
        """!
        @brief Collect trigger ability instances currently available in valid zones.

        This method creates runtime trigger wrappers but does not itself test
        trigger conditions; event-time trigger matching is handled by the event
        capture/trigger-processing pipeline.

        @param event Optional event associated with the created trigger instances.
        @return Trigger abilities exposed by cards in usable zones.
        """
        from ..game_actions.data_structs.ability import TriggerAbility, TriggerAbilityDefinition

        triggers: list[TriggerAbility] = []

        for card in self.get_cards():
            zone = card.get_zone()

            for ability_def in card.get_trigger_defs(self).values():
                if not isinstance(ability_def, TriggerAbilityDefinition):
                    continue

                if ability_def.is_usable_in_zone(zone):
                    triggers.append(
                        TriggerAbility(
                            ability_def,
                            card,
                            card.get_controller(self),
                            event=event,
                        )
                    )

        return triggers

    def get_triggered_abilities(self, event: GameEvent | None = None) -> list[TriggerAbility]:
        """!
        @brief Compatibility alias for `get_trigger_abilities`.

        @param event Optional event associated with the trigger instances.
        @return Currently available trigger ability instances.
        """
        return self.get_trigger_abilities(event)