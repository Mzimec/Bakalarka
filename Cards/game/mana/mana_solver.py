"""Mana-payment strategies that plan costs without mutating the game."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from dataclasses import dataclass
from ..game_actions.data_structs.decision_option import DecisionOption

if TYPE_CHECKING:
    from .mana_value import ManaRequirement
    from ..game_state import State, Player
    from ..game_actions.data_structs.game_action import ManaAbilityAction


@dataclass(frozen=True)
class ManaSolverResult(DecisionOption):
    """!
    @brief Result of planning a mana payment.

    An immutable data class representing a proposed mana payment plan.
    It does not mutate any game state on its own — it only carries the
    information needed for the caller (typically `AbilityExecutionPlan`)
    to later materialise (actually perform) the plan.

    @var mana_plan
        Tuple of actions (`ManaAbilityAction`) that need to be activated
        to produce the required mana. If the payment comes purely from
        the pool, this tuple will be empty.
    @var future_state
        The game state this plan applies to (in the current
        implementation this is typically the same state that was passed
        in — planning does not mutate state).
    @var payment
        Representation of the actual payment (e.g. an `ImmutableManaPool`
        or another object describing how much and what kind of mana was
        used). Defaults to `None`.
    @var life_payment
        Amount of life paid as part of the payment (e.g. for Phyrexian
        mana). Defaults to 0.
    """

    mana_plan: tuple[ManaAbilityAction]
    future_state: State
    payment: object = None
    life_payment: int = 0


class ManaSolver(ABC):
    """!
    @brief Abstract base for mana-payment planning strategies.

    Concrete implementations define different ways to build a plan for
    paying a given mana requirement — e.g. purely from the current pool,
    or also by activating mana sources on the battlefield. All
    implementations must be side-effect free with respect to game
    state — any mutation happens only when the returned plan is actually
    executed.
    """

    @abstractmethod
    def get_mana_plan(
        self, mana_req: ManaRequirement, controller: Player, state: State,
        *, reserved=frozenset(),
    ) -> ManaSolverResult | None:
        """!
        @brief Attempts to find a payment plan for the given mana requirement.

        @param mana_req The mana requirement that needs to be paid.
        @param controller The player making the payment (owner of the
               mana pool and, if applicable, the one activating mana
               sources).
        @param state The current game state to plan from (must not be
               mutated by this method).
        @param reserved Sources unavailable for activation because another cost uses them.
               Every solver accepts this keyword; pool-only solvers activate no sources.
        @return A `ManaSolverResult` instance describing the found plan,
                or `None` if no payment plan could be built.
        """
        ...


class PoolManaSolver(ManaSolver):
    """!
    @brief Finds an exact payment from already-produced mana without mutating the pool.

    This solver does not activate any mana sources — it only checks
    whether the requirement can be covered by mana the player already
    has in their mana pool.
    """

    def get_mana_plan(self, mana_req, controller, state, *, reserved=frozenset()):
        """!
        @brief Tries to cover the requirement purely from the player's mana pool.

        @param mana_req The mana requirement to cover.
        @param controller The player whose `mana_pool` is used.
        @param state The current game state (returned unchanged as
               `future_state`, since nothing gets activated).
        @return A `ManaSolverResult` with an empty `mana_plan` (no
                actions are needed) and the found payment, or `None` if
                the pool cannot cover the requirement.
        """
        from .mana_value import generate_mana_options_from_req_pool

        # Take the first valid mana combination from the pool that
        # satisfies the requirement. The generator may yield several
        # options, but the first one found is good enough here.
        payment = next(generate_mana_options_from_req_pool(mana_req, controller.mana_pool), None)
        return None if payment is None else ManaSolverResult((), state, payment)


class SourceActivatingManaSolver(ManaSolver):
    """!
    @brief Pays from the pool and activates legal battlefield mana sources.

    Planning is side-effect free — no abilities are actually activated
    here. The returned mana actions are later materialised by
    `AbilityExecutionPlan` inside the casting cost transaction, so a
    failed payment rolls back both the source activations and the rest
    of the cost.
    """

    def __init__(self, *, deduplicate_equivalent=True):
        self.deduplicate_equivalent = deduplicate_equivalent

    def get_mana_plan(self, mana_req, controller, state, *, reserved=frozenset()):
        """!
        @brief Builds a payment plan combining the mana pool and source activation.

        Steps:
        1. First tries whether the requirement is already covered by the
           mana pool alone (`PoolManaSolver`) — the cheapest and fastest
           path.
        2. If not, checks that the given `state` can report available
           mana sources (`get_mana_sources`), then uses `ManaGenerator`
           to propose a combination of sources that need to be
           activated.
        3. For each step of the generated plan, finds the corresponding
           ability on the source and builds a concrete game action
           (`ManaAbilityAction`) from it.

        @param mana_req The mana requirement to cover.
        @param controller The player whose pool and battlefield are used.
        @param state The current game state, used to read available mana
               sources (this method does not mutate it).
        @param reserved A set of sources/mana that are pre-reserved and
               should not be used by this solver (e.g. because they are
               needed for another cost in the same transaction).
        @return A `ManaSolverResult` with the tuple of actions needed to
                produce the missing mana and the corresponding payment,
                or `None` if no plan could be built (missing source
                getter, generator found no solution, or some ability
                could not be found/applied).
        """
        from .mana_generator import ManaGenerator
        from .mana_value import ImmutableManaPool

        # First try the cheapest option — purely from the pool.
        pooled = PoolManaSolver().get_mana_plan(mana_req, controller, state)
        if pooled is not None:
            return pooled

        # If the state can't report available mana sources, there's no
        # way to build an activation plan — bail out immediately.
        getter = getattr(state, "get_mana_sources", None)
        if not callable(getter):
            return None

        # ManaGenerator proposes a sequence of steps (source -> produced
        # mana) that together cover the remainder of the requirement not
        # already covered by the pool.
        plan = ManaGenerator(deduplicate_equivalent=self.deduplicate_equivalent).generate(
            mana_req,
            state,
            controller,
            reserved=reserved,
            mana_pool=controller.mana_pool,
        )
        if plan is None:
            return None

        from ..game_actions.generation.command_action_builder import chosen_action

        mana_actions = []
        payment = {}
        for step in plan.steps:
            # Track the aggregate payment by color/type as well, in case
            # the generator doesn't return its own `plan.payment`.
            payment[step.produces] = payment.get(step.produces, 0) + 1
            if step.source is None:
                # A step with no source (e.g. mana that's already
                # "virtually" available) doesn't require an extra game
                # action.
                continue
            source = step.source
            ability = source.try_find_ability(step.ability_key, state)
            if ability is None:
                # The ability wasn't found on the source (e.g. state
                # changed in the meantime) — the plan is invalid.
                return None
            try:
                mana_actions.append(chosen_action(ability, state, targets=()))
            except ValueError:
                # Building the concrete action failed (e.g. invalid
                # targets or activation conditions) — discard the plan.
                return None

        return ManaSolverResult(
            tuple(mana_actions),
            state,
            # If the generator returned its own, more precise payment
            # representation, use it; otherwise build the payment from
            # the locally tallied counts.
            plan.payment if plan.payment is not None else ImmutableManaPool(payment),
        )