"""Turn progression, turn-based actions and priority-window execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction, PassPriorityAction
    from ..game_actions.resolution.action_processor import ActionProcessor

from ..enums import *
from ..game_actions.data_structs.game_action import PassPriorityAction


@dataclass
class Turn:
    """!
    @brief Mutable turn counter and current phase state.

    `_phase_order` defines the engine's normal phase progression. Advancing
    beyond cleanup starts a new turn and increments `number`.
    """

    phase: TurnPhase = TurnPhase.UNTAP
    number: int = field(default=1, kw_only=True)

    _phase_order: list[TurnPhase] = field(
        default_factory=lambda: [
            TurnPhase.UNTAP,
            TurnPhase.UPKEEP,
            TurnPhase.DRAW,
            TurnPhase.PRECOMBAT_MAIN,
            TurnPhase.BEGIN_COMBAT,
            TurnPhase.DECLARE_ATTACKERS,
            TurnPhase.DECLARE_BLOCKERS,
            TurnPhase.FIRST_COMBAT_DAMAGE,
            TurnPhase.SECOND_COMBAT_DAMAGE,
            TurnPhase.END_COMBAT,
            TurnPhase.POSTCOMBAT_MAIN,
            TurnPhase.END_STEP,
            TurnPhase.CLEANUP,
        ]
    )

    def advance_phase(self) -> bool:
        """!
        @brief Advance to the next phase in normal turn order.

        @return `True` when advancing started a new turn, otherwise `False`.
        """
        idx = self._phase_order.index(self.phase)
        idx += 1

        if idx >= len(self._phase_order):
            self.phase = self._phase_order[0]
            self.number += 1
            return True

        self.phase = self._phase_order[idx]
        return False


@dataclass(frozen=True)
class PriorityWindowContext:
    """!
    @brief Context controlling which actions are legal in a priority window.

    @var allow_sorcery_speed
        Whether sorcery-speed actions are permitted in this window.
    """

    allow_sorcery_speed: bool


class PriorityWindow:
    """!
    @brief Run a complete priority exchange until the stack and passes stabilize.

    Players receive priority in active-player-first order. A successful action
    clears previous passes and the acting player retains priority. When all
    players pass with a non-empty stack, the top object resolves and the active
    player receives priority again. The window closes once all players pass on
    an empty stack.
    """

    def __init__(
        self, state: State, context: PriorityWindowContext, processor: ActionProcessor
    ) -> None:
        self.state = state
        self.context = context
        self.processor = processor

        self.players = self._order_players(state.players, state.active_player)
        self.current_index = 0

        # Store player identity rather than hashable player objects so simple
        # dataclass-based players/controllers can participate as well.
        self.passed_players: set[int] = set()
        self._sync_priority()

    def run(self) -> None:
        """!
        @brief Run the priority exchange until it closes or the game ends.
        """
        while not _game_over(self.state):
            player = self.current_player
            action = self._get_player_action(player)

            if action is None or isinstance(action, PassPriorityAction):
                self._pass(player)
                self._advance_priority()
            else:
                self._process_action(action)

            if self._should_resolve_stack():
                self._resolve_top_stack()

            if self._should_close():
                return

    def _get_player_action(self, player: Player) -> GameAction:
        """!
        @brief Ask the player's controller for its next priority action.

        Returning `None` is intentionally treated as passing priority so simple
        AI and test controllers need not construct an explicit pass action.
        """
        return player.get_action(self.state)

    def _process_action(self, action: GameAction) -> None:
        """!
        @brief Execute an action and reset passes when it succeeds.
        """
        results = self.processor.process(self.state, action)

        if results and not results[0].success:
            return

        # Any successful action breaks the current consecutive-pass sequence.
        self.passed_players.clear()

        # The player taking an action keeps priority until they explicitly pass.
        self._sync_priority()

    def _pass(self, player: Player) -> None:
        """!
        @brief Record that a player has passed in the current pass sequence.
        """
        self.passed_players.add(id(player))

    def _all_players_passed(self) -> bool:
        """!
        @brief Check whether every player has passed consecutively.
        """
        return len(self.passed_players) >= len(self.players)

    def _should_resolve_stack(self) -> bool:
        """!
        @brief Check whether consecutive passes should resolve the top stack item.
        """
        return self._all_players_passed() and not self.state.stack.is_empty()

    def _resolve_top_stack(self) -> None:
        """!
        @brief Resolve the top stack object and return priority to the active player.
        """
        self.processor.executor.resolve(
            self.state,
            self.state.stack.pop(),
        )

        self.passed_players.clear()
        self.current_index = 0
        self._sync_priority()

    def _should_close(self) -> bool:
        """!
        @brief Check whether all players passed with an empty stack.
        """
        return self._all_players_passed() and self.state.stack.is_empty()

    @property
    def current_player(self) -> Player:
        """!
        @brief Player currently holding priority.
        """
        return self.players[self.current_index]

    def _advance_priority(self) -> None:
        """!
        @brief Pass priority to the next player in APNAP order.
        """
        self.current_index += 1

        if self.current_index >= len(self.players):
            self.current_index = 0

        self._sync_priority()

    def _sync_priority(self) -> None:
        """!
        @brief Mirror local priority state into `state.priority` when present.
        """
        priority = getattr(self.state, "priority", None)

        if priority is not None:
            priority.current_player = self.current_player
            priority.passed_players = {
                player
                for player in self.players
                if id(player) in self.passed_players
            }

    def _order_players(
        self,
        players: list[Player],
        active_player: Player,
    ) -> list[Player]:
        """!
        @brief Rotate player order so the active player acts first.

        @return Active-player-first player ordering.
        """
        i = players.index(active_player)
        return players[i:] + players[:i]


class PhaseController(ABC):
    """!
    @brief Base controller for one turn phase or step.

    A normal phase performs its phase-start event and mandatory turn-based
    actions inside one deferred-trigger boundary, settles the resulting state,
    then processes those triggers before opening the phase's priority window.
    """

    @property
    @abstractmethod
    def priority_context(self) -> PriorityWindowContext:
        """!
        @brief Priority restrictions used after this phase's turn-based actions.
        """
        pass

    def run(self, state: State, processor: ActionProcessor) -> None:
        """!
        @brief Execute phase-start processing and then open priority.

        Trigger handling is deferred while the phase event and mandatory actions
        are performed so all resulting state changes settle before those triggers
        are processed.
        """
        previous = getattr(processor.executor, "_pending_triggers", None)
        processor.executor._pending_triggers = []

        try:
            _phase_event(state, processor)
            self.execute_with_processor(state, processor)
            processor.executor.settle(state)
        finally:
            pending = processor.executor._pending_triggers
            processor.executor._pending_triggers = previous

        # Preserve an enclosing deferral boundary when this phase is itself
        # running inside a larger atomic operation.
        if previous is not None:
            previous.extend(pending)
        elif not _game_over(state):
            processor.executor._trigger_resolver.process(state, pending)

        if _game_over(state):
            return

        PriorityWindow(
            state,
            self.priority_context,
            processor=processor,
        ).run()

    def execute_with_processor(self, state, processor):
        """!
        @brief Execute the phase's mandatory actions with processor access.

        Subclasses that need operation-resolution services can override this
        wrapper instead of `execute_turn_based_actions()`.
        """
        self.execute_turn_based_actions(state)

    @abstractmethod
    def execute_turn_based_actions(self, state: State) -> None:
        """!
        @brief Perform mandatory turn-based actions for this phase.
        """
        pass


class UntapPhaseController(PhaseController):
    """!
    @brief Perform the untap step, which does not normally grant priority.
    """

    @property
    def priority_context(self) -> PriorityWindowContext:
        """!
        @brief Return the untap step's non-sorcery priority context.
        """
        # The normal phase controller does not use this because untap overrides
        # run() and never opens a priority window.
        return PriorityWindowContext(allow_sorcery_speed=False)

    def run(self, state: State, processor: ActionProcessor) -> None:
        """!
        @brief Begin the turn, perform untapping and defer the phase event.

        Untap has no priority window. Its phase-start event is therefore deferred
        for later settlement rather than processed immediately here.
        """
        if hasattr(state, "begin_turn"):
            state.begin_turn()

        self.execute_turn_based_actions(state)
        _phase_event(state, processor, deferred=True)

    def execute_turn_based_actions(self, state: State) -> None:
        """!
        @brief Untap eligible permanents controlled by the active player.
        """
        if hasattr(state, "query_cards"):
            from helper.query_system.query import EqQuery
            from ..game_state.registers.card_register import IK_ZONE, IK_CONTROLLER

            permanents = state.query_cards(
                EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                & EqQuery(IK_CONTROLLER, state.active_player)
            )
        else:
            permanents = state.active_player.battlefield.values()

        skipped = set()

        # Skip markers are tied to a card incarnation. If the card changed zones
        # in the meantime, the marker no longer applies to the new incarnation.
        for card in state.get_cards() if hasattr(state, "get_cards") else ():
            marker = getattr(card, "_skip_untap", None)

            if marker is not None and marker[1] is state.active_player:
                if marker[0] == card.zone_revision:
                    skipped.add(card)

                del card._skip_untap

        for permanent in permanents:
            if hasattr(permanent, "is_tapped"):
                if (
                    hasattr(permanent, "has_keyword")
                    and permanent.has_keyword(state, "doesn't untap")
                ):
                    continue

                if permanent in skipped:
                    continue

                permanent.is_tapped = False


class PrecombatMainPhaseController(PhaseController):
    """!
    @brief Main-phase controller allowing sorcery-speed actions.
    """

    @property
    def priority_context(self) -> PriorityWindowContext:
        return PriorityWindowContext(allow_sorcery_speed=True)

    def execute_turn_based_actions(self, state: State) -> None:
        pass


class EndStepPhaseController(PhaseController):
    """!
    @brief End-step controller with a normal instant-speed priority window.
    """

    @property
    def priority_context(self) -> PriorityWindowContext:
        return PriorityWindowContext(allow_sorcery_speed=False)

    def execute_turn_based_actions(self, state: State) -> None:
        pass


class DrawPhaseController(PhaseController):
    """!
    @brief Perform the active player's mandatory draw.
    """

    @property
    def priority_context(self) -> PriorityWindowContext:
        return PriorityWindowContext(allow_sorcery_speed=False)

    def execute_turn_based_actions(self, state: State) -> None:
        """!
        @brief Legacy direct implementation of the mandatory draw.
        """
        player = state.active_player

        # Minimal test states may intentionally omit deck support.
        if hasattr(player, "deck"):
            if player.deck:
                player.draw(state)
            else:
                player.health = 0

    def execute_with_processor(self, state, processor):
        """!
        @brief Perform the mandatory draw through the ordinary resolution engine.
        """
        if not hasattr(state.active_player, "deck"):
            return

        if (
            getattr(state, "skip_first_draw", False)
            and state.turn.number == 1
            and len(state.players) == 2
        ):
            return

        from ..operations.card_operations import DrawCardOperation
        from ..game_actions.data_structs.game_action import ResolutionContext

        context = ResolutionContext(
            controller=state.active_player,
        )

        _run_operations(
            state,
            processor,
            [DrawCardOperation(context)],
        )


class SimplePhaseController(PhaseController):
    """!
    @brief Default controller for phases with no mandatory action yet.
    """

    def __init__(self, allow_sorcery_speed: bool = False) -> None:
        self._context = PriorityWindowContext(allow_sorcery_speed)

    @property
    def priority_context(self) -> PriorityWindowContext:
        return self._context

    def execute_turn_based_actions(self, state: State) -> None:
        return None


class CleanupPhaseController(PhaseController):
    """!
    @brief Perform cleanup and repeat it if state-based actions require priority.

    CR 514 requires another cleanup step after any exceptional cleanup priority
    window. This controller therefore loops until cleanup produces no relevant
    state changes and the stack remains empty.
    """

    @property
    def priority_context(self):
        return PriorityWindowContext(allow_sorcery_speed=False)

    def execute_turn_based_actions(self, state):
        """!
        @brief Remove marked damage and deathtouch damage markers.
        """
        for card in state.get_cards():
            card.state.damage_marked = 0
            card.state.damage_by_deathtouch = False

    def run(self, state, processor):
        """!
        @brief Perform cleanup passes until the game reaches a stable cleanup state.
        """
        # CR 514: repeat cleanup after an exceptional priority window.
        while True:
            player = state.active_player

            excess = (
                max(0, len(player.hand) - player.maximum_hand_size)
                if player.maximum_hand_size is not None
                else 0
            )

            if excess:
                choose = getattr(
                    player.controller,
                    "choose_discards",
                    None,
                )

                discards = tuple(
                    choose(state, player, excess)
                    if choose
                    else tuple(player.hand.values())[-excess:]
                )

                if (
                    len(discards) != excess
                    or len(set(discards)) != excess
                    or any(card not in player.hand.values() for card in discards)
                ):
                    raise ValueError(
                        "Choose the required number of distinct cards from your hand."
                    )

                from ..operations.card_operations import MoveCardOperation
                from ..game_actions.data_structs.game_action import ResolutionContext

                context = ResolutionContext(
                    controller=player,
                )

                # Cleanup discards happen before normal trigger/event processing
                # for this cleanup pass, so their events are explicitly deferred.
                _run_operations(
                    state,
                    processor,
                    [
                        MoveCardOperation(
                            context,
                            card,
                            ZoneType.GRAVEYARD,
                        )
                        for card in discards
                    ],
                    deferred=True,
                )

            self.execute_turn_based_actions(state)

            # Expiring effects and damage removal occur before the SBA check.
            processor.executor.settle(state)

            if _game_over(state):
                return

            changed = bool(
                getattr(
                    processor.executor,
                    "last_sba_events",
                    (),
                )
            )

            if not changed and state.stack.is_empty():
                return

            # Cleanup normally grants no priority. This flag marks the exceptional
            # window caused by cleanup events/SBAs so action legality can distinguish it.
            state.cleanup_priority = True

            try:
                PriorityWindow(
                    state,
                    self.priority_context,
                    processor,
                ).run()
            finally:
                state.cleanup_priority = False


class CombatPhaseController(SimplePhaseController):
    """!
    @brief Execute combat-specific turn-based actions for combat phases.
    """

    def execute_with_processor(self, state, processor):
        """!
        @brief Dispatch mandatory combat processing for the current combat phase.
        """
        combat = state.combat
        phase = state.turn.phase

        if phase == TurnPhase.BEGIN_COMBAT:
            combat.begin()

        elif phase == TurnPhase.DECLARE_ATTACKERS:
            player = state.active_player
            choose = getattr(
                player.controller,
                "choose_attackers",
                None,
            )

            events = combat.declare_attackers(
                player,
                choose(state, player) if choose else {},
            )

            _publish_events(
                state,
                processor,
                events,
            )

        elif phase == TurnPhase.DECLARE_BLOCKERS:
            for player in state.players:
                if player is state.active_player:
                    continue

                choose = getattr(
                    player.controller,
                    "choose_blockers",
                    None,
                )

                events = combat.declare_blockers(
                    player,
                    choose(state, player) if choose else {},
                )

                _publish_events(
                    state,
                    processor,
                    events,
                )

        elif phase in {
            TurnPhase.FIRST_COMBAT_DAMAGE,
            TurnPhase.SECOND_COMBAT_DAMAGE,
        }:
            operations = combat.damage_operations(
                first_strike=phase == TurnPhase.FIRST_COMBAT_DAMAGE
            )

            # Combat damage is simultaneous, so replacements and trigger capture
            # must observe one shared pre/post batch state.
            processor.executor.resolve_simultaneous(
                state,
                operations,
            )


def _game_over(state):
    """!
    @brief Return whether the current state represents a finished game.
    """
    return getattr(
        state,
        "is_game_over",
        any(player.health <= 0 for player in state.players),
    )


def _publish_events(state, processor, events):
    """!
    @brief Publish events and immediately route their captured triggers.
    """
    processor.executor._emit_events(
        state,
        events,
    )
    processor.executor._process_triggers(
        state,
        events,
    )


def _run_operations(state, processor, operations, deferred=False):
    """!
    @brief Execute a group of operations through the appropriate engine path.

    When `deferred` is false the operations are wrapped in a fixed execution
    plan and resolved normally. Deferred execution bypasses immediate event
    publication and stores the resulting events on the state for later settle.
    """
    from ..game_actions.data_structs.game_action import (
        FixedExecutionPlan,
        ScheduledResolution,
        ResolutionContext,
    )

    if deferred:
        events = processor.executor._operation_executor.execute_batch(
            state,
            operations,
        )
        state.defer_events(events)

    else:
        processor.executor.resolve(
            state,
            ScheduledResolution(
                FixedExecutionPlan(
                    list(operations)
                ),
                ResolutionContext(),
            ),
        )


def _phase_event(state, processor, deferred=False):
    """!
    @brief Create and publish or defer the current `phase_started` event.

    Trigger eligibility is captured immediately, even when publication is
    deferred, so later state changes do not alter which abilities triggered.
    """
    if not hasattr(state, "defer_events"):
        return

    from ..game_actions.resolution.event_bus import GameEvent, capture_event

    event = capture_event(
        state,
        GameEvent(
            "phase_started",
            None,
            state.active_player,
            {
                "phase": state.turn.phase,
                "turn": state.turn.number,
            },
        ),
    )

    if deferred:
        state.defer_events([event])
    else:
        _publish_events(
            state,
            processor,
            [event],
        )


class GameLoop:
    """!
    @brief Advance the game through phases until a player loses.

    The loop delegates phase-specific behavior to `PhaseController` instances,
    performs phase-transition housekeeping and skips phases that do not exist
    for the current game/combat state.
    """

    def __init__(
        self,
        phase_map: dict[TurnPhase, PhaseController] | None,
        processor: ActionProcessor,
    ) -> None:
        """!
        @brief Create the game loop.

        @param phase_map Optional custom controller map; defaults to standard phases.
        @param processor Action processor used for resolution and priority actions.
        """
        self.phase_map = phase_map or self.default_phase_map()
        self.processor = processor

    @staticmethod
    def default_phase_map() -> dict[TurnPhase, PhaseController]:
        """!
        @brief Build the default controller mapping for all turn phases.
        """
        phases = {
            phase: SimplePhaseController()
            for phase in TurnPhase
        }

        phases[TurnPhase.UNTAP] = UntapPhaseController()
        phases[TurnPhase.DRAW] = DrawPhaseController()
        phases[TurnPhase.PRECOMBAT_MAIN] = PrecombatMainPhaseController()
        phases[TurnPhase.POSTCOMBAT_MAIN] = PrecombatMainPhaseController()
        phases[TurnPhase.END_STEP] = EndStepPhaseController()
        phases[TurnPhase.CLEANUP] = CleanupPhaseController()

        for phase in (
            TurnPhase.BEGIN_COMBAT,
            TurnPhase.DECLARE_ATTACKERS,
            TurnPhase.DECLARE_BLOCKERS,
            TurnPhase.FIRST_COMBAT_DAMAGE,
            TurnPhase.SECOND_COMBAT_DAMAGE,
            TurnPhase.END_COMBAT,
        ):
            phases[phase] = CombatPhaseController()

        return phases

    def run(self, state: State) -> None:
        """!
        @brief Run phases continuously until the game ends.
        """
        while not self._is_game_over(state):
            self.step(state)

    def step(self, state: State) -> None:
        """!
        @brief Run one phase and advance to the next applicable phase.

        This single-phase entry point allows tests and examples to inspect the
        resulting state between phase transitions.
        """
        if self._is_game_over(state):
            return

        # In a two-player game the starting player may skip the first draw.
        if (
            state.turn.phase == TurnPhase.DRAW
            and getattr(state, "skip_first_draw", False)
            and state.turn.number == 1
            and len(state.players) == 2
        ):
            state.turn.advance_phase()
            return

        controller = self.phase_map[state.turn.phase]
        controller.run(
            state,
            self.processor,
        )

        if (
            state.turn.phase == TurnPhase.END_COMBAT
            and hasattr(state, "combat")
        ):
            state.combat.end()

        if hasattr(state, "synchronise_registers"):
            state.synchronise_registers()

        if self._is_game_over(state):
            return

        # Mana pools empty as phases and steps end.
        for player in state.players:
            if hasattr(player, "mana_pool"):
                player.mana_pool.substract(
                    dict(player.mana_pool)
                )

        if state.turn.advance_phase():
            state.switch_active_player()

        # The same first-draw skip must also be respected when normal phase
        # advancement lands directly on DRAW.
        if (
            state.turn.phase == TurnPhase.DRAW
            and getattr(state, "skip_first_draw", False)
            and state.turn.number == 1
            and len(state.players) == 2
        ):
            state.turn.advance_phase()

        # If no attackers were declared, there is no blockers step.
        if (
            state.turn.phase == TurnPhase.DECLARE_BLOCKERS
            and hasattr(state, "combat")
            and not state.combat.had_attackers
        ):
            state.turn.phase = TurnPhase.END_COMBAT

        # There is no first-strike combat-damage step unless at least one
        # combatant currently deals first- or double-strike damage.
        if (
            state.turn.phase == TurnPhase.FIRST_COMBAT_DAMAGE
            and hasattr(state, "combat")
        ):
            if not state.combat.has_first_strike_damage():
                state.turn.advance_phase()

    def _is_game_over(self, state: State) -> bool:
        """!
        @brief Delegate game-over detection to the shared helper.
        """
        return _game_over(state)