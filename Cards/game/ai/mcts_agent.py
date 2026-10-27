"""Live search controller with a separate trusted world-construction boundary.

Legacy providers take detached information; current-position bridges prepare a
sanitized snapshot from the local request. Neither search policies nor the tree
receive the live request. Every sampled root must match the player's information.
"""
from dataclasses import asdict
from itertools import islice
from random import Random
from time import perf_counter, process_time

from game.ai.decision_maker import DecisionMaker, DecisionResult
from game.ai.information_set_search import InformationSetSearch, SampledRootMismatch
from game.ai.knowledge import PlayerKnowledge, PlayerEvent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.search_simulator import SimulationBudgetExceeded
from game.ai.search_statistics import SearchStatistics
from game.ai.generation_candidates import CandidateFrontier
from game.ai.structured_values import ValueStore


class MCTSAgent(DecisionMaker):
    """!
    @brief Return locally generated options; search never receives the live request.

    Unsupported worlds and incomplete searches use the observation-only fallback.
    Unsupported action adapters and engine errors remain explicit errors. Oversized
    menus fail by default; the opt-in emergency policy chooses the first legal
    option and disables search for the rest of that game, with explicit diagnostics.
    No silent top-k, actual hidden-card copying, external LLM or match RNG use.
    The generation policy must agree with the supplied simulators' policy.
    """
    def __init__(self, *, world_provider, search=None, fallback=None, policy_for=None,
                 seed=0, max_options=4096, max_decisions=10000, search_setup=True,
                 allow_incomplete_menu_fallback=False):
        if not callable(world_provider) and not callable(getattr(world_provider, "prepare", None)):
            raise TypeError("Supply a world provider or trusted current-position bridge")
        if type(seed) is not int:
            raise ValueError("A deterministic integer search seed is required")
        if type(max_options) is not int or max_options < 1:
            raise ValueError("max_options must be a positive integer")
        if type(max_decisions) is not int or max_decisions < 1:
            raise ValueError("max_decisions must be a positive integer")
        if type(search_setup) is not bool:
            raise TypeError("search_setup must be boolean")
        if type(allow_incomplete_menu_fallback) is not bool:
            raise TypeError("allow_incomplete_menu_fallback must be boolean")
        self.allow_incomplete_menu_fallback = allow_incomplete_menu_fallback
        self.max_decisions, self.search_setup = max_decisions, search_setup
        self.on_search_decision = None
        # Optional self-play behavior, supplied detached information/results only.
        # The default continues to execute the search's recommended root action.
        self.search_choice = None
        self.world_provider = world_provider
        self.search = search if search is not None else InformationSetSearch(opponent_policy=ObservationOpponentPolicy())
        self.fallback = fallback if fallback is not None else ObservationOpponentPolicy()
        self.policy_for = policy_for if policy_for is not None else lambda request: None
        self.seed, self.max_options = seed, max_options
        self.knowledge = PlayerKnowledge()
        self._game_token = self._player = self._participants = None
        self._reset_trace()

    def _reset_trace(self):
        reset = getattr(self.world_provider, "reset", None)
        if reset is not None:
            reset()
        reset_search = getattr(self.search, "reset", None)
        if reset_search is not None:
            reset_search()
        self._search_session = object()
        self.value_store = ValueStore()
        self.recall = PlayerRecall()
        self.last_information = self.last_search = None
        self._rng = Random(self.seed)
        self.decisions = 0
        self.search_statistics = SearchStatistics()
        self._search_disabled_reason = None

    def search_summary(self):
        """!
        @brief Return cumulative detached search, decision and fallback counters.
        """
        return self.search_statistics.summary()

    def close_search(self):
        """!
        @brief Release an optional persistent pool after a match or interactive run.
        """
        close = getattr(self.search, 'close', None)
        if close is not None:
            close()

    def _bind(self, game_token, player, participants=None):
        new_game = ((self._game_token is not None and game_token is not None and self._game_token is not game_token)
                    or (self._player is not None and self._player != player)
                    or (participants is not None and self._participants is not None and self._participants != participants))
        if new_game:
            self._reset_trace()
            self._participants = None
        if game_token is not None:
            self._game_token = game_token
            self.knowledge.bind(game_token, player)
        elif new_game:
            self._game_token = None
            self.knowledge.bind(None, player)
        self._player = player
        if participants is not None:
            self._participants = participants

    def receive_player_event(self, event, *, game_token, player):
        """!
        @brief Record only this player projected event in the current session.
        """
        if type(event) is not PlayerEvent:
            raise TypeError("Search recall accepts only projected player events")
        self._bind(game_token, player)
        self.knowledge.observe(event)
        self.recall = self.recall.append("event", asdict(event))
        if (event.kind in ('library_shuffled', 'card_drawn')
                or event.from_zone in ('DECK', 'HAND') or event.to_zone in ('DECK', 'HAND')):
            invalidate = getattr(self.search, 'invalidate_belief', None) or getattr(self.search, 'invalidate_model', None)
            if invalidate is not None:
                invalidate()

    def _decide(self, request):
        started = perf_counter()
        cpu_started = process_time()
        token = request.state.knowledge_token if request.state is not None else None
        self._bind(token, request.player.idx, tuple(request.participants))
        if self.decisions >= self.max_decisions:
            from game.ai.simple_agent import DecisionLimitReached
            raise DecisionLimitReached("MCTS live decision budget exhausted.")
        self.last_search = None
        stages = {}
        stage_started = perf_counter()
        candidate_model = getattr(self.policy_for, 'candidate_model', None)
        candidate_width, menu_complete = 0, True
        frontier = (CandidateFrontier(candidate_model.generate(request, self.policy_for(request)), self.max_options)
                    if candidate_model else None)
        stream = iter(request.option_space(self.policy_for(request))) if frontier is None else None
        try:
            if frontier is None:
                options = tuple(islice(stream, self.max_options + 1))
            else:
                candidate_width = min(candidate_model.initial_width, self.max_options)
                options, menu_complete = frontier.take(candidate_width)
        finally:
            close = getattr(stream if frontier is None else frontier, "close", None)
            if close is not None:
                close()
        overflow = len(options) > self.max_options
        if overflow:
            if not self.allow_incomplete_menu_fallback:
                raise SimulationBudgetExceeded(f"{type(request).__name__} exceeds max_options={self.max_options}")
            # Explicit emergency policy: first generated legal action. We did
            # not observe the full menu, so subsequent recall cannot be replayed
            # as a complete information set. Disable search until a new game.
            # Never run MCTS over this arbitrary truncated menu.
            options = options[:1]
            self._search_disabled_reason = "incomplete_menu_history"
        stages["generation_ms"] = (perf_counter() - stage_started) * 1000
        stage_started = perf_counter()
        projected = DecisionProjector(request, options, self.recall, store=self.value_store,
            candidate_model=candidate_model.identity if candidate_model else None,
            menu_complete=menu_complete, candidate_width=candidate_width).build()
        info = projected.information
        self.last_information = info
        fallback = self.fallback(info)
        stages["projection_and_fallback_ms"] = (perf_counter() - stage_started) * 1000
        projected.bind(fallback)  # Validate even if the search later succeeds.
        choice, reason = fallback, "no_world_provider_match"
        search_attempted = False
        unsupported_detail = None
        world_preparation = None
        if overflow:
            reason = "option_budget"
        elif len(info.actions) == 1 and info.menu_complete:
            reason = "forced_choice"
            remember = getattr(self.world_provider, "remember_boundary", None)
            if remember is not None and request.state is not None:
                anchor_started = perf_counter()
                remember(request, info, self.recall, self.knowledge)
                stages["continuation_anchor_ms"] = (perf_counter() - anchor_started) * 1000
        elif self._search_disabled_reason is not None:
            reason = self._search_disabled_reason
        elif request.state is None:
            reason = "setup"
        elif not self.search_setup and info.current.request_kind in {"MulliganRequest", "MulliganBottomRequest"}:
            reason = "setup_policy"
        else:
            stage_started = perf_counter()
            prepare = getattr(self.world_provider, "prepare", None)
            factory = (prepare(request, info, self.recall, self.knowledge) if prepare is not None
                       else self.world_provider(info))
            stages["snapshot_ms"] = (perf_counter() - stage_started) * 1000
            world_preparation = getattr(self.world_provider, "preparation", None)
            if factory is None:
                reason = getattr(self.world_provider, "unsupported_reason", None) or reason
                unsupported_detail = getattr(self.world_provider, "unsupported_detail", None)
            if factory is not None:
                search_attempted = True
                stage_started = perf_counter()
                try:
                    reuse = {"reuse_context": self._search_session} if getattr(self.search, "tree_reuse_size", 0) else {}
                    self.last_search = self.search.search(factory, info, seed=self._rng.getrandbits(64), fallback=fallback, **reuse)
                except SampledRootMismatch:
                    # Do not swallow rule errors or replay divergence: only an
                    # explicitly incompatible root has this recoverable meaning.
                    reason = "sampled_root_mismatch"
                else:
                    choice = self.last_search.choice
                    if not self.last_search.used_fallback and self.search_choice is not None:
                        choice = self.search_choice(info, self.last_search)
                        if choice not in {row.action for row in self.last_search.estimates}:
                            raise ValueError('Self-play choice must be a searched root action')
                    reason = ((self.last_search.fallback_reason or self.last_search.stop_reason)
                              if self.last_search.used_fallback else "searched")
                stages["search_ms"] = (perf_counter() - stage_started) * 1000
        if choice not in info.actions and candidate_model is not None:
            width = max(candidate_width, len(self.last_search.estimates))
            frontier = CandidateFrontier(candidate_model.generate(request, self.policy_for(request)), self.max_options)
            try:
                while True:
                    options, menu_complete = frontier.take(min(width, self.max_options))
                    expanded = DecisionProjector(request, options, self.recall, store=self.value_store,
                        candidate_model=candidate_model.identity, menu_complete=menu_complete, candidate_width=width).build()
                    if choice in expanded.information.actions:
                        projected, info = expanded, expanded.information
                        self.last_information = info
                        break
                    if menu_complete or width >= self.max_options:
                        raise ValueError('Searched candidate is not in the live legal frontier')
                    width = min(width * 2, self.max_options)
            finally:
                frontier.close()
        option = projected.bind(choice)
        self.recall = info.recall.append("action", choice.describe())
        if type(request).__name__ == 'ScryRequest':
            invalidate = getattr(self.search, 'invalidate_belief', None) or getattr(self.search, 'invalidate_model', None)
            if invalidate is not None:
                invalidate()
        worker_cpu = (self.last_search.metrics.parallel_worker_cpu_ms
                      if self.last_search is not None and self.last_search.metrics.parallel_backend == 'process' else 0.0)
        detail = {
            "reason": reason, "used_search": search_attempted,
            "used_fallback": reason not in {"searched", "forced_choice"},
            "options": len(info.actions), "information_key": info.key,
            "information_contract": 3 if info.candidate_model else 2,
            "knowledge_key": info.knowledge_key, "menu_digest": info.menu_digest,
            'candidate_model': info.candidate_model, 'candidate_width': info.candidate_width,
            "search": asdict(self.last_search) if self.last_search is not None else None,
            "menu_complete": not overflow and info.menu_complete,
            "search_disabled_reason": self._search_disabled_reason,
            "request_kind": type(request).__name__, "stage_ms": stages,
            "unsupported_detail": unsupported_detail,
            "cpu_ms": (process_time() - cpu_started) * 1000 + worker_cpu,
            "world_preparation": world_preparation,
        }
        self.decisions += 1
        elapsed = (perf_counter() - started) * 1000
        self.search_statistics.record(detail, elapsed)
        if self.on_search_decision is not None:
            self.on_search_decision(request, {**detail, "elapsed_ms": elapsed})
        return DecisionResult(option, {"mcts": detail})
