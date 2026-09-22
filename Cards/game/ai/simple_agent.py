"""Bounded, deterministic decisions through the engine's public action pipeline."""

from __future__ import annotations

from game.ai.decision_maker import (
    AbilityDecisionRequest,
    AbilityResolutionRequest,
    DeclareAttackersRequest,
    DeclareBlockersRequest,
    DiscardRequest,
    MulliganBottomRequest,
    MulliganRequest,
    PriorityDecisionRequest,
)

from game.game_actions.generation.decision_abstraction.options import (
    DeclareAttackersOption, DeclareBlockersOption, MulliganOption, MulliganBottomOption,
    DiscardOption, AbilityResolutionOption,
)

from typing import TYPE_CHECKING, Callable

from ..enums import CardType
from ..game_actions.data_structs.game_action import PassPriorityAction
from .decision_maker import DecisionResult, ModularDecisionMaker

if TYPE_CHECKING:
    from ..game_state import State, Player
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
    validated by the shared decision-generation pipeline, so this agent never
    has to reimplement targeting/legality rules itself; it only ranks
    the legal options the engine hands back.
    """

    def __init__(self, *, max_decisions: int = 10000, log: Callable | None = None,
                 auto_pass: bool = True) -> None:
        """!
        @brief Initializes the agent with a decision budget and optional logger.

        @param max_decisions Upper bound on the total number of decisions
               (`_record` calls) this agent may make before
               `DecisionLimitReached` is raised. Used to keep runaway
               simulations bounded.
        @param log Optional callback `(state, player, kind, details)`
               invoked on every recorded decision, e.g. for debugging or
               replay logging. Defaults to a no-op.
        @param auto_pass Skip priority selection in quiet windows. Passes still
               count against the decision budget and are logged normally.
        """
        if type(auto_pass) is not bool:
            raise ValueError("auto_pass must be a boolean.")
        self.auto_pass = auto_pass
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

    def _auto_pass_result(self, request, *, reason="only_pass_candidate"):
        self._record(request.state, request.player, "pass", auto_pass=True, reason=reason)
        return DecisionResult(PassPriorityAction(request.player),
                              {"auto_pass": True, "auto_pass_reason": reason})

    def decide_priority(self, request: PriorityDecisionRequest) -> DecisionResult[GameAction]:
        """Prefer a land, otherwise rank bounded options from the priority pipeline."""
        state, player = request.state, request.player
        window = (state.turn.number, state.turn.phase)
        if self._window != window:
            # New turn/phase: forget which abilities were already tried,
            # since they may now be legal/desirable again.
            self._window = window
            self._attempted.clear()
        from ..game_actions.generation.decision_abstraction.requests import PriorityDecisionRequest
        from ..game_actions.generation.decision_abstraction.policies import AbilityGenerationPolicy, PriorityGenerationPolicy
        from ..game_actions.generation.pruning.pruning_strategy import LimitPruning, FilterPruning
        from ..rules.lands import LandPlayAction

        policy = PriorityGenerationPolicy(
            ability_gp=AbilityGenerationPolicy(pruning=LimitPruning(24)),
            ability_space_ps=FilterPruning(
                lambda ability: (ability.source.command_id, ability.key) not in self._attempted
            ),
            include_mana=False, include_concede=False,
        )
        choices = []
        for action in PriorityDecisionRequest(state, player).option_space(policy):
            if isinstance(action, PassPriorityAction):
                continue
            if isinstance(action, LandPlayAction):
                self._record(state, player, "land", source=action.card)
                return DecisionResult(action)
            key = (action.source.command_id, action.action_key)
            choices.append((self.score(state, player, action), key, action))
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
                return DecisionResult(action)
        elif self.auto_pass:
            return self._auto_pass_result(request)
        self._record(state, player, "pass")
        return DecisionResult(PassPriorityAction(player))

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

    def decide_ability(self, request: AbilityDecisionRequest) -> DecisionResult[GameAction]:
        """Rank the legal actions of the requested ability without changing state."""
        state, trigger, choices = request.state, request.ability, request.options
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
        return DecisionResult(action)

    def decide_attackers(self, request: DeclareAttackersRequest) -> DecisionResult[DeclareAttackersOption]:
        """Choose the all-attack proposal through the shared legality pipeline."""
        state, player = request.state, request.player
        from .combat_strategies import AllAttackersStrategy
        from ..game_actions.generation.decision_abstraction.policies import DeclareAttackersPolicy
        option = next(iter(request.option_space(DeclareAttackersPolicy(strategy=AllAttackersStrategy()))), None)
        if option is None:
            raise ValueError("No legal attacker proposal was generated.")
        result = dict(option.declarations)
        self._record(state, player, "attack", attackers=tuple(result))
        return DecisionResult(option)

    def decide_blockers(self, request: DeclareBlockersRequest) -> DecisionResult[DeclareBlockersOption]:
        """Prefer the greedy proposal, falling back to legal bounded alternatives."""
        state, player = request.state, request.player
        from .combat_strategies import BoundedBlockersStrategy
        from ..game_actions.generation.decision_abstraction.policies import DeclareBlockersPolicy
        option = next(iter(request.option_space(DeclareBlockersPolicy(strategy=BoundedBlockersStrategy()))), None)
        if option is None:
            raise ValueError("No legal blocker proposal was generated within the search budget.")
        result = dict(option.declarations)
        self._record(state, player, "block", blockers=tuple(result.items()))
        return DecisionResult(option)

    def decide_mulligan(self, request: MulliganRequest) -> DecisionResult[MulliganOption]:
        """Keep two to five lands; respect the request and the two-mulligan budget."""
        state, player = request.state, request.player
        mulligans_taken = request.mulligans_taken
        lands = sum(card.is_type(state, CardType.LAND) for card in player.hand.values())
        return DecisionResult(MulliganOption(request.can_mulligan and mulligans_taken < 2 and not 2 <= lands <= 5))

    def decide_mulligan_bottom(self, request: MulliganBottomRequest) -> DecisionResult[MulliganBottomOption]:
        """Bottom the highest-cost cards in deterministic order."""
        state, player = request.state, request.player
        count = request.count
        return DecisionResult(MulliganBottomOption(self._rank_cards(state, player.hand.values())[:count]))

    @staticmethod
    def _rank_cards(state, cards):
        return tuple(sorted(cards, key=lambda card: -(
            card.get_mana_cost(state).cmc() if card.get_mana_cost(state) is not None else 0
        )))


    def decide_discard(self, request: DiscardRequest) -> DecisionResult[DiscardOption]:
        """Use the shared card ranking for cleanup discards."""
        state, player = request.state, request.player
        count = request.count
        return DecisionResult(DiscardOption(self._rank_cards(state, player.hand.values())[:count]))

    def decide_ability_resolution(self, request: AbilityResolutionRequest) -> DecisionResult[AbilityResolutionOption]:
        cards = (self._rank_cards(request.state, request.candidates)
                 if request.kind == "discard" else request.candidates)
        return DecisionResult(AbilityResolutionOption(cards[:request.count]))
