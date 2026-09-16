"""Bounded, deterministic decisions through the engine's public action pipeline."""

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING, Callable

from game.console.command_choices import legal_plans, UnsupportedCommandDefinition
from ..enums import CardType, TurnPhase, ZoneType
from ..game_actions.data_structs.game_action import PassPriorityAction
from .decision_maker import (
    ModularDecisionMaker,
    DecisionResult,
)
from game.rules.lands import LandPlayAction, land_play_error

if TYPE_CHECKING:
    from ..game_state import State, Player, Card
    from ..game_actions.data_structs.game_action import GameAction


class DecisionLimitReached(RuntimeError):
    """!
    @brief The experiment exhausted its decision budget; this is not a game draw.

    Raised by `SimpleAgent` when the number of decisions it has made
    exceeds `max_decisions`. This is a safety valve against runaway
    simulations/loops, not a legitimate game outcome — callers should
    treat it as an experiment-level failure rather than a draw or loss.
    """


class SimpleAgent(ModularDecisionMaker):
    """!
    @brief A reproducible baseline player, not a search or learning algorithm.

    Implements `DecisionMaker` using simple, deterministic heuristics
    instead of any lookahead search or learning. Only the player's hand
    and public zones are consulted when choosing actions — no hidden
    information is used. Target choices and payments are generated and
    validated by the real engine (`legal_plans`), so this agent never
    has to reimplement targeting/legality rules itself; it only ranks
    the legal options the engine hands back.
    """

    def __init__(self, *, max_decisions: int = 10000, log: Callable | None = None) -> None:
        """!
        @brief Initializes the agent with a decision budget and optional logger.

        @param max_decisions Upper bound on the total number of decisions
               (`_record` calls) this agent may make before
               `DecisionLimitReached` is raised. Used to keep runaway
               simulations bounded.
        @param log Optional callback `(state, player, kind, details)`
               invoked on every recorded decision, e.g. for debugging or
               replay logging. Defaults to a no-op.
        """
        self.max_decisions = max_decisions
        self.decisions = 0
        self.log = log or (lambda *args, **kwargs: None)
        # Tracks the current (turn, phase) window so `_attempted` can be
        # reset whenever the game moves to a new decision window.
        self._window = None
        # Ability keys already tried (and declined) in the current
        # window, so the agent doesn't loop offering the same
        # unhelpful action over and over within one priority window.
        self._attempted = set()

    def _record(self, state, player, kind, **details):
        """!
        @brief Bookkeeping/logging hook called on every decision made by the agent.

        Increments the decision counter, enforces `max_decisions`, and
        forwards the decision to the configured `log` callback.

        @param state Current game state at the time of the decision.
        @param player The player the decision was made for.
        @param kind Short string tag describing the decision type
               (e.g. "land", "action", "pass", "trigger", "attack",
               "block").
        @param details Arbitrary extra keyword data describing the
               decision (source card, targets, etc.), passed through to
               `log`.
        @throws DecisionLimitReached If the decision budget has been
                exceeded.
        """
        self.decisions += 1
        if self.decisions > self.max_decisions:
            raise DecisionLimitReached("Decision budget exhausted.")
        self.log(state, player, kind, details)

    def _priority_action(self, state: State, player: Player) -> GameAction:
        """!
        @brief Play a land, develop the board, or choose a bounded legal action.

        Decision order:
        1. If a land can legally be played from hand, play it
           immediately (lands are never scored against other actions).
        2. Otherwise, gather candidate abilities from cards in hand,
           battlefield, and graveyard controlled by this player
           (excluding mana abilities, which are only ever used as part
           of paying a cost, never floated speculatively).
        3. For each candidate ability, ask the engine for a small,
           bounded sample of legal cost/target plans (`legal_plans`) and
           build a concrete `GameAction` for each combination found.
        4. Score every candidate action with `score` and pick the best
           one, provided its score is positive (i.e. worth doing at
           all).
        5. If nothing scores positively, pass priority.

        @param state Current game state.
        @param player The player who needs to act.
        @return A generated `GameAction` (land play, ability/spell
                action) or a normal `PassPriorityAction` if nothing
                worthwhile is available.
        """
        window = (state.turn.number, state.turn.phase)
        if self._window != window:
            # New turn/phase: forget which abilities were already tried,
            # since they may now be legal/desirable again.
            self._window = window
            self._attempted.clear()
        for card in player.hand.values():
            if card.is_type(state, CardType.LAND) and land_play_error(card, player, state) is None:
                self._record(state, player, "land", source=card)
                return LandPlayAction(card, player)
        # Candidate sources for non-land actions: everything in hand,
        # plus this player's own permanents on the battlefield and cards
        # they control in the graveyard (e.g. flashback-style effects).
        sources = list(player.hand.values())
        sources.extend(
            card
            for card in state.get_cards(from_zones=[ZoneType.BATTLEFIELD, ZoneType.GRAVEYARD])
            if card.get_controller(state) is player
        )
        choices = []
        for card in sources:
            for definition in sorted(
                card.get_ability_defs(state).values(), key=lambda item: item.key
            ):
                key = (card.command_id, definition.key)
                if key in self._attempted or definition.is_mana_ability:
                    continue  # Mana is planned inside payment, never floated speculatively.
                if definition.validation_error(card, player, state):
                    continue
                ability = definition.to_ability(card, player)
                try:
                    # Only sample a handful of cost plans; we don't need
                    # every possible payment, just proof that at least
                    # one legal cost plan exists.
                    costs = list(islice(legal_plans(ability, state, cost=True), 4))
                    if not costs:
                        continue
                    # Similarly bound the number of target/binding plans
                    # explored per ability, to keep this deterministic
                    # agent's per-decision cost roughly constant even on
                    # boards with many legal targets.
                    for _, plan in islice(legal_plans(ability, state), 24):
                        action = ability.to_game_action(costs[0][1], plan)
                        choices.append((self.score(state, player, action), key, action))
                except UnsupportedCommandDefinition:
                    # Some ability/cost shapes aren't supported by the
                    # console's plan generator; just skip those rather
                    # than failing the whole decision.
                    continue
        if choices:
            score, key, action = max(choices, key=lambda item: item[0])
            if score > 0:
                # Remember this ability was tried this window so we
                # don't keep re-offering it if state doesn't change
                # enough to alter the outcome.
                self._attempted.add(key)
                self._record(
                    state,
                    player,
                    "action",
                    source=action.source,
                    ability=action.action_key,
                    targets=self._targets(action),
                )
                return action
        self._record(state, player, "pass")
        return PassPriorityAction(player)

    def _decide_priority_action(self, state, player):
        return DecisionResult(
            self._priority_action(state, player)
        )

    @staticmethod
    def _targets(action):
        """!
        @brief Flattens an action's target binding into a single tuple.

        @param action The `GameAction` whose `action_generator.binding`
               (a mapping of slot -> group -> targets) should be
               flattened.
        @return A flat tuple of every target across all slots and
                groups, in iteration order. Used purely for logging.
        """
        return tuple(
            target
            for groups in action.action_generator.binding.values()
            for group in groups.values()
            for target in group
        )

    def score(self, state, player, action) -> float:
        """!
        @brief Ranks legal candidates using visible board information only.

        Heuristic scoring, roughly:
        - Base value: spells are worth more than activated abilities.
        - Creature spells get bonus value proportional to power +
          toughness.
        - The action is flagged as "harmful" either by inspecting its
          effect bindings for damage/destroy/return/pacifism/discard-like
          keywords, or by a hardcoded name list of known removal/pacifism
          effects.
        - Each target is then scored based on whose side it's on: a
          "hostile" target (an opposing slot, or a harmful effect not
          explicitly targeting your own stuff) is rewarded if it hits an
          opponent and penalized if it hits the acting player (and vice
          versa for non-hostile/beneficial targeting).

        @param state Current game state.
        @param player The player on whose behalf the action is being
               considered (used to judge whether targets are
               friendly/hostile).
        @param action The candidate `GameAction` to evaluate.
        @return A heuristic score; higher is better. Nonpositive scores
                mean the action is not worth taking voluntarily (the
                agent will pass instead).
        """
        types = action.source.get_types(state)
        score = 25.0 if action.ability.is_spell else 5.0
        if action.ability.is_spell and CardType.CREATURE in types:
            score += (
                10
                + (action.source.get_power(state) or 0)
                + (action.source.get_toughness(state) or 0)
            )
        effects = action.action_generator.effects.sequence
        # Heuristically detect "harmful" effects by checking whether any
        # bound effect's class name + key contains a telltale keyword.
        harmful = any(
            any(
                word in (type(binding.effect).__name__ + binding.effect.key).lower()
                for word in ("damage", "destroy", "returntarget", "pacifism", "discard", "counterspell")
            )
            for binding in effects
        )
        # Fallback for known removal/pacifism-style cards whose effect
        # classes don't obviously match the keyword check above.
        harmful |= action.source.name in {
            "Pacifism",
            "Waterknot",
            "Sleep",
            "Compound Fracture",
            "Cruel Cut",
        }
        for slot, groups in action.action_generator.binding.items():
            for group in groups.values():
                for target in group:
                    # A target may itself be a player, or a card whose
                    # controller determines ownership.
                    owner = target if target in state.players else target.get_controller(state)
                    hostile = slot.startswith("other") or (harmful and not slot.startswith("own"))
                    desired = owner is not player if hostile else owner is player
                    score += 20 if desired else -60
        return score

    def choose_trigger_action(self, state, trigger, choices):
        """!
        @brief Selects a required trigger's legal targets without reading console input.

        Used when a triggered ability requires the controller to choose
        among several legal target/binding options. Delegates to `score`
        to pick the best-ranked choice, using the trigger's controller
        as the perspective player.

        @param state Current game state.
        @param trigger The triggered ability being resolved (its
               `controller`, `source`, and `key` are used).
        @param choices Iterable of legal candidate actions to choose
               from.
        @return The chosen candidate action (highest score).
        """
        action = max(
            choices, key=lambda candidate: self.score(state, trigger.controller, candidate)
        )
        self._record(
            state,
            trigger.controller,
            "trigger",
            source=trigger.source,
            ability=trigger.key,
            targets=self._targets(action),
        )
        return action

    def choose_attackers(self, state: State, player: Player) -> dict:
        """!
        @brief Declares all legal positive-power attackers against the opposing player.

        Simplistic combat policy: attack with every legal attacker that
        has power > 0, always attacking the single opposing player
        (no support for multiplayer target selection or planeswalkers).

        @param state Current game state.
        @param player The attacking player.
        @return Mapping of attacking card -> defending player, suitable
                for the engine's attacker-declaration API.
        """
        opponent = next(other for other in state.active_players if other is not player)
        result = {
            card: opponent
            for card in state.combat.legal_attackers(player)
            if (card.get_power(state) or 0) > 0
        }
        self._record(state, player, "attack", attackers=tuple(result))
        return result

    def choose_blockers(self, state: State, player: Player) -> dict:
        """!
        @brief Assigns legal blockers, preferring trades and respecting menace.

        Two passes:
        1. First handles "must be blocked by all" attackers: assigns
           every legal, not-yet-used blocker to them (skipping if the
           attacker has menace and fewer than two blockers are
           available, since menace requires at least two blockers).
        2. Then, for remaining attackers (processed from highest power
           to lowest, to prioritize blocking the biggest threats),
           assigns the highest-power available blockers — one blocker
           normally, or two if the attacker has menace — as long as
           enough legal blockers exist.

        @param state Current game state.
        @param player The blocking player.
        @return Mapping of blocking card -> attacking card it blocks,
                suitable for the engine's blocker-declaration API.
        """
        result = {}
        for attacker in state.combat.attackers:
            if attacker.has_keyword(state, "must be blocked by all"):
                legal = [
                    card
                    for card in state.combat.legal_blockers(player, attacker)
                    if card not in result
                ]
                if not attacker.has_keyword(state, "menace") or len(legal) >= 2:
                    result.update((card, attacker) for card in legal)
        for attacker in sorted(
            state.combat.attackers, key=lambda card: -(card.get_power(state) or 0)
        ):
            if attacker in result.values():
                # Already assigned blockers via the "must be blocked by
                # all" pass above.
                continue
            candidates = [
                card for card in state.combat.legal_blockers(player, attacker) if card not in result
            ]
            # Prefer the strongest available blockers first, breaking
            # ties deterministically by command_id for reproducibility.
            candidates.sort(key=lambda card: (-(card.get_power(state) or 0), card.command_id))
            count = 2 if attacker.has_keyword(state, "menace") else 1
            if len(candidates) >= count:
                result.update((card, attacker) for card in candidates[:count])
        self._record(state, player, "block", blockers=tuple(result.items()))
        return result

    def choose_mulligan(self, state, player, mulligans_taken):
        """!
        @brief Keeps hands with two to five lands; takes at most two mulligans.

        @param state Current game state.
        @param player The player deciding on their opening hand.
        @param mulligans_taken Number of mulligans already taken this
               game.
        @return `True` if the player should mulligan again, `False` if
                the current hand should be kept. Always keeps once two
                mulligans have already been taken, regardless of land
                count.
        """
        lands = sum(card.is_type(state, CardType.LAND) for card in player.hand.values())
        return mulligans_taken < 2 and not 2 <= lands <= 5

    def choose_mulligan_bottom(self, state, player, count):
        """!
        @brief Bottoms the most expensive cards deterministically after the final mulligan.

        @param state Current game state.
        @param player The player who must put `count` cards on the
               bottom of their library (London mulligan-style).
        @param count Number of cards to bottom.
        @return Tuple of the `count` cards with the highest converted
                mana cost (cards with no mana cost are treated as CMC 0),
                chosen deterministically.
        """
        return tuple(
            sorted(
                player.hand.values(),
                key=lambda card: -(
                    card.get_mana_cost(state).cmc() if card.get_mana_cost(state) is not None else 0
                ),
            )[:count]
        )

    def choose_discards(self, state, player, count):
        """!
        @brief Uses the same deterministic card valuation for cleanup discards.

        Reuses `choose_mulligan_bottom`'s "discard the most expensive
        cards first" heuristic for end-of-turn hand-size discards.

        @param state Current game state.
        @param player The player who must discard down to their hand
               size limit.
        @param count Number of cards to discard.
        @return Tuple of `count` cards to discard, chosen by the same
                highest-CMC-first heuristic.
        """
        return self.choose_mulligan_bottom(state, player, count)