"""Validate strategy proposals before exposing them as selectable options."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from .requests import (DeclareAttackersRequest, DeclareBlockersRequest,
                       MulliganRequest, MulliganBottomRequest, DiscardRequest, AbilityResolutionRequest)
from .options import (DecisionOption, DeclareAttackersOption, DeclareBlockersOption,
                      MulliganOption, MulliganBottomOption, DiscardOption, AbilityResolutionOption)
from .policies import SelectionPolicy
from .selection_strategies import combat_view
from ..pruning.pruning_strategy import apply_pruning


class SelectionPipeline[R, T: DecisionOption](ABC):
    """!
    @brief Base pipeline that validates strategy-generated selection options.
    """
    option_type: type[T]

    def generate(self, request: R, policy: SelectionPolicy[R, T]) -> Iterator[T]:
        """!
        @brief Validate the request, filter legal strategy output, and apply pruning.
        @param request Selection request.
        @param policy Selection policy.
        @return Iterator over legal generated options.
        """
        self.validate_request(request)
        candidates = self._legal_candidates(request, policy)
        yield from apply_pruning(candidates, policy.pruning)

    def _legal_candidates(self, request: R, policy: SelectionPolicy[R, T]) -> Iterator[T]:
        """!
        @brief Filter strategy output to options legal for the request.
        @param request Selection request.
        @param policy Selection policy.
        @return Iterator over legal options.
        """
        for option in policy.strategy.generate(request):
            if not isinstance(option, self.option_type):
                raise TypeError(f"Strategy must yield {self.option_type.__name__}.")
            if self.is_legal(request, option):
                yield option

    @abstractmethod
    def validate_request(self, request: R) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        ...

    @abstractmethod
    def is_legal(self, request: R, option: T) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        ...


class DeclareAttackersPipeline(SelectionPipeline[DeclareAttackersRequest, DeclareAttackersOption]):
    """!
    @brief Pipeline validating attacker declaration options.
    """
    option_type = DeclareAttackersOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        request.state.combat.validate_attackers(request.player, {})

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        from ....rules.combat import CombatError
        try:
            request.state.combat.validate_attackers(request.player, option.declarations)
        except CombatError:
            return False
        return True


class DeclareBlockersPipeline(SelectionPipeline[DeclareBlockersRequest, DeclareBlockersOption]):
    """!
    @brief Pipeline validating blocker declaration options.
    """
    option_type = DeclareBlockersOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        from ....enums import TurnPhase
        state, player = request.state, request.player
        if (not state.combat.active or state.turn.phase != TurnPhase.DECLARE_BLOCKERS
                or not state.combat._attackers_declared or player is state.active_player
                or player not in state.active_players or player in state.combat._blockers_declared):
            raise ValueError("This player cannot declare blockers in the current combat step.")

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        from ....rules.combat import CombatError
        try:
            combat_view(request.state).validate_blockers(request.player, option.declarations)
        except CombatError:
            return False
        return True


class MulliganPipeline(SelectionPipeline[MulliganRequest, MulliganOption]):
    """!
    @brief Pipeline validating mulligan options.
    """
    option_type = MulliganOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        if type(request.mulligans_taken) is not int or request.mulligans_taken < 0:
            raise ValueError("Mulligan count must be a nonnegative integer.")

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        return not option.take_mulligan or request.can_mulligan


def validate_count(count: int, candidates: Iterable[object]) -> None:
    """!
    @brief Validate that a requested selection count is legal.
    @param count Requested number of objects.
    @param candidates Eligible candidates.
    """
    if type(count) is not int or not 0 <= count <= len(candidates):
        raise ValueError("Choose a valid number of eligible objects.")


def legal_cards(cards: Iterable[object], count: int, candidates: Iterable[object]) -> bool:
    """!
    @brief Return whether selected cards form a legal fixed-size subset.
    @param cards Selected cards.
    @param count Required count.
    @param candidates Eligible candidates.
    @return True when legal.
    """
    return (len(cards) == count and len(set(cards)) == count
            and all(card in candidates for card in cards))


class MulliganBottomPipeline(SelectionPipeline[MulliganBottomRequest, MulliganBottomOption]):
    """!
    @brief Pipeline validating mulligan-bottom card choices.
    """
    option_type = MulliganBottomOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        validate_count(request.count, request.player.hand)

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        return legal_cards(option.cards, request.count, tuple(request.player.hand.values()))


class DiscardPipeline(SelectionPipeline[DiscardRequest, DiscardOption]):
    """!
    @brief Pipeline validating discard choices.
    """
    option_type = DiscardOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        validate_count(request.count, request.player.hand)

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        return legal_cards(option.cards, request.count, tuple(request.player.hand.values()))


class AbilityResolutionPipeline(SelectionPipeline[AbilityResolutionRequest, AbilityResolutionOption]):
    """!
    @brief Pipeline validating card choices during ability resolution.
    """
    option_type = AbilityResolutionOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Selection request.
        """
        validate_count(request.count, request.candidates)
        if len(set(request.candidates)) != len(request.candidates):
            raise ValueError("Eligible objects must be distinct.")

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Selection request.
        @param option Option to validate.
        @return True when legal.
        """
        return legal_cards(option.cards, request.count, request.candidates)
