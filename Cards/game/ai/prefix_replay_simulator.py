"""Faster replay of a trusted fixed scenario; no live-state cloning or threads.

Earlier prefix decisions regenerate only their selected option and reconcile
memory without constructing full observations. The parent decision being acted
on, and the returned child, retain the reference backend's complete processing.
"""
from itertools import islice

from game.ai.decision_maker import DecisionResult
from game.ai.observation import PlayerObservationBuilder
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import SearchSimulator, _Replay, ReplayDiverged, SimulationBudgetExceeded


class _PrefixReplay(_Replay):
    """!
    @brief Reconstruct selected prefixes and validate their continuation boundary.
    """
    def decide(self, request):
        # Recall must include private observations at *every* own decision.
        # Until immutable observation traces are cached, use strict replay for
        # this explicit mode instead of silently dropping historical evidence.
        """!
        @brief Validate the native decision prefix and choose or expose its boundary.
        """
        if self.simulator.track_information:
            return super().decide(request)
        # The last recorded choice is the parent of the requested child. Verify
        # its complete menu/observations before executing it. Earlier history
        # belongs to an immutable scenario already checked on its first visit.
        if self.cursor >= len(self.history) - 1:
            return super().decide(request)
        recorded = self.history[self.cursor]
        state = request.state
        header = (type(request).__name__, request.player.idx,
                  state.turn.number if state is not None else None,
                  state.turn.phase.name if state is not None else None)
        expected = recorded.address
        if header != (expected.kind, expected.player, expected.turn, expected.phase):
            raise ReplayDiverged(f"Prefix decision {self.cursor} changed its request")
        options = iter(request.option_space(self.simulator.policy_for(request)))
        try:
            for index, option in enumerate(islice(options, self.simulator.max_options + 1)):
                if index == self.simulator.max_options:
                    raise SimulationBudgetExceeded("Selected prefix choice exceeds max_options")
                if ChoiceDescriptor.from_option(option) == recorded.choice:
                    if state is not None:
                        # Observations previously updated memory for every
                        # participant, including the nonacting player.
                        for player in request.participants:
                            PlayerObservationBuilder(state, player).observe_knowledge()
                    self.cursor += 1
                    return DecisionResult(option)
        finally:
            close = getattr(options, "close", None)
            if close is not None:
                close()
        raise ReplayDiverged(f"Selected choice missing at prefix decision {self.cursor}")


class PrefixReplaySimulator(SearchSimulator):
    """!
    @brief Same node/advance/fork contract, with lighter earlier-prefix checks.

    Use only with deterministic, side-effect-free generation and a fixed scenario,
    catalog, policies and budgets. Unlike SearchSimulator, this backend does not
    revalidate every *earlier* menu and full observation, nor exhaust unused options.
    It verifies the selected descriptor/request and preserves player memory there.
    Use the reference backend for strict divergence auditing and differential tests.
    Every branch still constructs a fresh engine and re-executes the prefix's rules;
    this reduces replay overhead but does not provide constant-time continuations.
    With track_information=True, use the strict path to retain every historical
    private decision observation until verified observation traces can be reused.
    """
    def _make_replay(self, history):
        return _PrefixReplay(self, history)
