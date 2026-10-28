"""Reference search simulator using deterministic replay through the real engine.

Each advance reconstructs a fresh scenario and replays its immutable decision
prefix. Suspension may occur inside any nested resolution request. The unwound
runtime is discarded, never resumed; only copied observations survive it.
This correctness backend intentionally trades replay time for branch isolation.
"""
from dataclasses import dataclass, field, asdict
from hashlib import sha256
from functools import cached_property
from itertools import islice
from collections import OrderedDict
import json
from time import perf_counter

from game.ai.decision_maker import DecisionMaker, DecisionResult
from game.ai.knowledge import PlayerKnowledge
from game.ai.observation import observation, decision_observation
from game.ai.observation import PlayerObservationBuilder
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_actions import PlayerAction
from game.ai.search_decision import DecisionProjector
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_recall import PlayerRecall, PlayerInformation
from game.game_state.turn_history import TurnHistoryUnavailable
from game.ai.structured_values import StructuredJSON, ValueStore, decode, structural_key


class ReplayDiverged(RuntimeError):
    """!
    @brief Scenario or generation no longer reproduces the recorded decisions.
    """


class SimulationBudgetExceeded(RuntimeError):
    """!
    @brief An explicit simulation limit was reached, not a game outcome.
    """


class HistorySamplingExhausted(SimulationBudgetExceeded):
    """!
    @brief No history-compatible world was accepted within the sampling budget.
    """


class SimulationHistoryUnavailable(SimulationBudgetExceeded):
    """!
    @brief A rule needs historical information absent from the sampled snapshot.
    """


@dataclass(frozen=True)
class WorldInitializationMetrics:
    """!
    @brief Detached acceptance and rejection diagnostics for world preparation.
    @var sampling_attempts Number of sampling attempts recorded by this component.
    @var rejected_samples Number of rejected samples recorded by this component.
    @var prefix_advance_attempts Number of prefix advance attempts recorded by this component.
    @var population_updates Number of population updates recorded by this component.
    @var population_survivors Number of population survivors recorded by this component.
    @var population_size Population size retained by this record.
    @var population_distinct_origins Population distinct origins retained by this record.
    @var population_refreshes Number of population refreshes recorded by this component.
    @var mismatch_reasons Mismatch reasons retained by this record.
    """
    sampling_attempts: int = 0
    rejected_samples: int = 0
    prefix_advance_attempts: int = 0
    population_updates: int = 0
    population_survivors: int = 0
    population_size: int = 0
    population_distinct_origins: int = 0
    population_refreshes: int = 0
    mismatch_reasons: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class DecisionAddress:
    """!
    @brief Private replay address identifying a native decision boundary.
    @var kind Explicit record/action kind used by this protocol.
    @var player Index of the player owning these facts.
    @var turn Turn retained by this record.
    @var phase Phase retained by this record.
    @var menu_digest Menu digest retained by this record.
    @var observation_digest Observation digest retained by this record.
    """
    kind: str
    player: int
    turn: int | None
    phase: str | None
    menu_digest: str
    observation_digest: str


@dataclass(frozen=True)
class RecordedChoice:
    """!
    @brief Complete replay choice with an optional candidate-frontier width.
    @var address Address retained by this record.
    @var choice Recommended action or explicitly reported fallback choice.
    @var candidate_limit Candidate limit retained by this record.
    """
    address: DecisionAddress
    choice: ChoiceDescriptor
    candidate_limit: int | None = None


@dataclass(frozen=True)
class _Projection:
    """!
    @brief Detached facts at an already visited deterministic-world prefix.

    @var header Header retained by this record.
    @var descriptors Descriptors retained by this record.
    @var address Address retained by this record.
    @var views Frozen views; a forced prefix record may retain only the actor,
        because it is consumed solely while replaying a recorded action.
    @var prior_recall Prior recall retained by this record.
    @var information Immutable actor observation, recall and offered actions.
    @var bindings Bindings retained by this record.
    """
    header: tuple
    descriptors: tuple
    address: DecisionAddress
    views: tuple
    prior_recall: PlayerRecall
    information: PlayerInformation
    bindings: tuple


@dataclass(frozen=True)
class SimulationNode:
    """!
    @brief Immutable boundary with viewer-filtered facts and local action descriptors.
    @var history Immutable complete-choice prefix reproducing this sampled boundary.
    @var decision Private replay address, or None at a terminal/cutoff.
    @var choices Complete native descriptors offered at this boundary.
    @var terminal Whether the native game reached a terminal outcome.
    @var cutoff Whether an explicit nonterminal simulator limit was reached.
    @var phase_steps Native phase progress counter for replay and simulator limits.
    @var _views Immutable viewer-specific projections retained for supported consumers.
    @var _owner Private identity token of the owning simulator.
    @var _recalls Immutable per-player memory snapshots.
    @var _action_bindings Visible-action to descriptor bindings local to this boundary.
    @var _projected_information Actor information reused without re-decoding views.
    @var _sparse Whether optional diagnostic views were omitted from this boundary.
    @var _candidate_order Deterministic admitted candidate descriptor order.
    """
    history: tuple[RecordedChoice, ...]
    decision: DecisionAddress | None
    choices: tuple[ChoiceDescriptor, ...]
    terminal: bool
    cutoff: bool
    phase_steps: int
    _views: tuple[tuple[int, str | StructuredJSON], ...] = field(repr=False)
    _owner: object = field(repr=False, compare=False)
    _recalls: tuple[tuple[int, PlayerRecall], ...] = field(default=(), repr=False)
    _action_bindings: tuple[tuple[PlayerAction, ChoiceDescriptor], ...] = field(default=(), repr=False)
    _projected_information: PlayerInformation | None = field(default=None, repr=False, compare=False)
    _sparse: bool = field(default=False, repr=False, compare=False)
    _candidate_order: tuple = field(default=(), repr=False, compare=False)

    def recall(self, player):
        """!
        @brief Return the immutable recall belonging to the requested player.
        """
        if not self._recalls:
            raise ValueError("Enable track_information to collect player recall")
        return dict(self._recalls)[player]

    def player_information(self):
        """!
        @brief Expose only current actor facts, recall and offered visible actions.
        """
        return self._player_information

    @cached_property
    def _player_information(self):
        # Only immutable player-facing values are retained. Callers still get
        # detached dictionaries from observation(); no runtime state is cached.
        if self._projected_information is not None:
            return self._projected_information
        current = ActingPlayerObservation.from_node(self)
        return PlayerInformation(current, self.recall(current.player), tuple(a for a, _ in self._action_bindings))

    def bind_action(self, action):
        """!
        @brief Bind an offered visible action to its local native descriptor.
        """
        if not self._recalls:
            raise ValueError("Enable track_information to bind player actions")
        for candidate, descriptor in self._action_bindings:
            if candidate == action:
                return descriptor
        raise ValueError("Player action is not offered in this world at this decision")

    def observation(self, player):
        """!
        @brief Return a detached player-specific view, including that player's memory.

        Search infrastructure may inspect sampled-world views. A player policy must
        only receive its own view, never the collection of other players' views.
        """
        for owner, encoded in self._views:
            if owner == player:
                return decode(encoded)
        raise ValueError("This simulator did not retain that player's diagnostic view")

    def observation_payload(self, player):
        """!
        @brief One immutable viewer-filtered payload, never the collection of views.
        """
        from game.ai.structured_values import payload
        for owner,encoded in self._views:
            if owner==player:
                return payload(encoded)
        raise ValueError("This simulator did not retain that player's diagnostic view")

    def observation_readonly(self, player):
        """!
        @brief Internal reader over one viewer's facts, with no deep JSON decode.
        """
        return self.observation_payload(player).value.readonly


class _DecisionBoundary(BaseException):
    """!
    @brief Private control signal; ordinary rule-error handlers must not swallow it.
    """


class ForcedBoundary:
    """!
    @brief Ephemeral native callback boundary; never stored in a search cache.

    Only the actor projection is eager. Observer views and the public immutable
    node are built if a horizon/budget/caller actually asks for them, before the
    engine resumes. The runtime request must not escape this callback.
    """
    terminal = cutoff = False

    def __init__(self, replay, request, address, descriptor, information, shared_characteristics):
        self.replay, self.request = replay, request
        self.history, self.decision = replay.history, address
        self.choices, self.phase_steps = (descriptor,), replay.phase_steps
        self._information = information
        self.shared_characteristics = shared_characteristics

    def player_information(self):
        """!
        @brief Expose only current actor facts, recall and offered visible actions.
        """
        return self._information

    def observation(self, player):
        """!
        @brief Return a detached player-specific view including that player memory.
        """
        return self.materialize().observation(player)

    def observation_readonly(self, player):
        """!
        @brief Read one viewer projection without deeply allocating an export.
        """
        return self.materialize().observation_readonly(player)

    @cached_property
    def _node(self):
        views = {player: StructuredJSON(view, self.replay.simulator.value_store)
                 for player, view in self.replay._view_data(self.request.state, self.request.participants, self.request,
                     exclude_player=self.request.player.idx, shared_characteristics=self.shared_characteristics)}
        views[self.request.player.idx] = self._information.current._json
        return SimulationNode(self.history, self.decision, self.choices, False, False,
            self.phase_steps, tuple(sorted(views.items())), self.replay.simulator._identity,
            self.replay.recall_snapshot(), ((self._information.actions[0], self.choices[0]),), self._information)

    def materialize(self):
        """!
        @brief Construct or expose the owned world or detached boundary needed by this consumer.
        """
        return self._node

    def detached(self):
        """!
        @brief Cache only immutable facts actually needed at this logical prefix.

        A search has a fixed root/horizon. Missing diagnostic views are never
        invented; native replay rehydrates them if an uncached suffix resumes.
        """
        if '_node' in self.__dict__:
            return self._node
        actor = self._information.current.player
        return SimulationNode(self.history, self.decision, self.choices, False, False,
            self.phase_steps, ((actor, self._information.current._json),), self.replay.simulator._identity,
            self.replay.recall_snapshot(), ((self._information.actions[0], self.choices[0]),),
            self._information, True)


class _ReplayController(DecisionMaker):
    """!
    @brief Receive projected events and forward decisions to the replay driver.
    """
    def __init__(self, driver):
        self.driver = driver
        self.knowledge = PlayerKnowledge()

    @property
    def sequential_combat(self):
        """! @brief Reconstruct the live controller's optional native combat frames. """
        return getattr(self.driver.simulator.policy_for, 'sequential_combat', False)

    def receive_player_event(self, event, *, game_token, player):
        """!
        @brief Record only this player projected event in the current session.
        """
        self.knowledge.bind(game_token, player)
        self.knowledge.observe(event)
        self.driver.observe_event(player, event)

    def _decide(self, request):
        return self.driver.decide(request)

    def decide(self, request):
        # Simulation decisions do not publish live-controller timing metrics.
        """!
        @brief Validate the native decision prefix and choose or expose its boundary.
        """
        return self._validated_decision(request)


class _Replay:
    """!
    @brief Own reconstruction history, player recalls and the active native frontier.
    """
    def __init__(self, simulator, history):
        self.simulator, self.history = simulator, history
        self.cursor = 0
        self.phase_steps = 0
        self.node = None
        self.follow = None
        self.follow_started = False
        self.coalesce_forced = False
        self.frontier = None
        self.candidate_complete = True
        self.candidate_prior = None
        self._combat_projection = None
        self.recalls = {0: PlayerRecall(), 1: PlayerRecall(), **dict(simulator.initial_recalls)}

    def observe_event(self, player, event):
        """!
        @brief Append only the projected player event to that actor immutable recall.
        """
        if self.simulator.track_information:
            self.recalls[player] = self.recalls[player].append("event", asdict(event), store=self.simulator.value_store)

    def recall_snapshot(self):
        """!
        @brief Return immutable per-player recalls detached from controller mutation.
        """
        return tuple(sorted(self.recalls.items())) if self.simulator.track_information else ()

    def views(self, state, participants, request=None):
        """!
        @brief Project and freeze the supported participant views at this decision.
        """
        return tuple((player, StructuredJSON(view, self.simulator.value_store) if self.simulator.structured_records
                      else json.dumps(view, sort_keys=state is not None))
                     for player, view in self._view_data(state, participants, request))

    def _view_data(self, state, participants, request=None, *, exclude_player=None, shared_characteristics=None):
        if state is None:
            return tuple((p.idx, {"self": p.idx, "setup": True,
                          "players": [{"id": q.idx, "name": q.name} for q in participants]}) for p in participants)
        from game.game_actions.generation.decision_abstraction.combat_frames import CombatNextRequest
        layout = None
        if (type(request) is CombatNextRequest and self.simulator.track_information
                and self.simulator.structured_records and exclude_player is None and shared_characteristics is None):
            # Only this native frame can execute between these callbacks. No
            # card, zone, event or knowledge changes until atomic completion.
            layout = (state, request.player, request.frame.creatures, request.frame.targets)
            cached = self._combat_projection
            if cached is not None and all(a is b for a, b in zip(cached[0], layout)):
                return tuple((p, view.value.readonly) for p, view in cached[1])
        else:
            self._combat_projection = None
        views = []
        shared_characteristics = {} if shared_characteristics is None else shared_characteristics
        for p in participants:
            if p.idx == exclude_player:
                continue
            # The acting policy always receives its own complete view. Search
            # also retains the root observer's view for leaf evaluation; other
            # views are diagnostic data and can be omitted in the owned world.
            if (self.simulator.view_mode == "search" and p.idx != self.simulator.observer
                    and (request is None or p is not request.player)):
                continue
            store = self.simulator.value_store if self.simulator.structured_records else None
            view = (decision_observation(request, shared_characteristics=shared_characteristics, projection_store=store)
                    if request is not None and p is request.player else observation(state, p,
                        update_knowledge=not self.simulator.track_information,
                        shared_characteristics=shared_characteristics, projection_store=store))
            # Diagnostic views of the nonacting player must not create extra
            # memories that a live controller never received. Tracked recall
            # changes only on projected events and that player's own decisions.
            evidence = getattr(p.controller, "initial_search_evidence", None)
            if evidence is not None:
                view["initial_search_evidence"] = evidence
            views.append((p.idx, view))
        if layout is not None:
            self._combat_projection = (layout, tuple((p, StructuredJSON(view, self.simulator.value_store))
                                                     for p, view in views))
        return tuple(views)

    def decide(self, request):
        """!
        @brief Validate the native decision prefix and choose or expose its boundary.
        """
        model = getattr(self.simulator.policy_for, 'candidate_model', None)
        if model is None:
            return self._decide_options(request)
        from game.ai.generation_candidates import CandidateFrontier
        previous = self.frontier, self.candidate_prior, self.candidate_complete
        self.frontier = CandidateFrontier(model.generate(request, self.simulator.policy_for(request)), self.simulator.max_options)
        self.candidate_prior = self.recalls[request.player.idx]
        width = (self.history[self.cursor].candidate_limit if self.cursor < len(self.history) else
                 self.simulator.candidate_widths.get(self.history)) or model.initial_width
        try:
            width = min(width, self.simulator.max_options)
            started = perf_counter() if self.simulator.profile_advance else 0
            role_combat = (getattr(model, 'combat_equivalence', False) and type(request).__name__
                           in {'DeclareAttackersRequest', 'DeclareBlockersRequest'})
            reservoir = min(16, self.simulator.max_options)
            prefetch = (self.simulator.track_information and getattr(model, 'native_ordering', False)
                        and self.simulator._candidate_cache_limit and not self.history and self.follow is None)
            if prefetch and role_combat:
                reservoir = model.combat_reservoir_limit(request, width, reservoir)
            reserve = max(width, reservoir) if prefetch else width
            options, self.candidate_complete = self.frontier.take(reserve, lookahead=not (role_combat and reserve > width))
            self.simulator.record_stage('candidate_generation', started)
            self.simulator._active_candidates = self, request
            if reserve > width:
                # Prepare the small root reservoir while the native callback is
                # already active. Publish exactly the live admitted prefix.
                wide = self._decide_options(request, options, expand_only=True)
                self.simulator._remember_candidate_menu(wide)
                node = self.simulator._candidate_prefix(wide, width)
                self.frontier.width = width
                self.candidate_complete = node.player_information().menu_complete
                return self._select(request, options[:width], node._candidate_order, node.decision,
                                    node._views, node._action_bindings, node.player_information())
            return self._decide_options(request, options)
        finally:
            self.frontier.close()
            self.frontier, self.candidate_prior, self.candidate_complete = previous
            self.simulator._active_candidates = None

    def expand_candidates(self, request, width):
        """!
        @brief Resume the native frontier to admit a wider candidate prefix.
        """
        started = perf_counter() if self.simulator.profile_advance else 0
        options, self.candidate_complete = self.frontier.take(width)
        self.simulator.record_stage('candidate_generation', started)
        self.recalls[request.player.idx] = self.candidate_prior
        # Widening resumes this exact native callback before any selected
        # action executes. The board and viewer permissions have not changed;
        # only admitted options and their bindings need to be projected again.
        from game.ai.generation_candidates import DeterministicCandidates
        model = getattr(self.simulator.policy_for, 'candidate_model', None)
        views = (self.node._views if self.node is not None and type(model) is DeterministicCandidates else None)
        node = self._decide_options(request, options, expand_only=True,
                                    existing_views=views)
        self.node = node
        return node

    def _decide_options(self, request, supplied_options=None, *, expand_only=False, existing_views=None):
        started = perf_counter() if self.simulator.profile_advance else 0
        # This is a hard diagnostic budget, never silent top-k pruning. Policies
        # can be supplied explicitly when an experiment intentionally uses proposals.
        options = (tuple(islice(request.option_space(self.simulator.policy_for(request)),
                               self.simulator.max_options + 1)) if supplied_options is None else supplied_options)
        if len(options) > self.simulator.max_options:
            raise SimulationBudgetExceeded(f"{type(request).__name__} exceeds max_options={self.simulator.max_options}")
        if not options:
            raise ReplayDiverged(f"No options for {type(request).__name__}")
        started = self.simulator.record_stage('generation', started, return_start=True)
        if (len(options) == 1 and self.candidate_complete and self.coalesce_forced and self.follow_started
                and self.cursor == len(self.history) and self.simulator.structured_records):
            return self._forced_decision(request, options[0])
        descriptors = tuple(ChoiceDescriptor.from_option(option) for option in options)
        started = self.simulator.record_stage('descriptors', started, return_start=True)
        state = request.state
        actor = request.player.idx
        header = (type(request).__name__, actor,
                  state.turn.number if state is not None else None,
                  state.turn.phase.name if state is not None else None)
        prefix = self.history[:self.cursor]
        model = getattr(self.simulator.policy_for, 'candidate_model', None)
        cache_key = prefix if model is None else (prefix, len(options), self.candidate_complete)
        cache = self.simulator._projections
        cached = cache.get(cache_key) if self.cursor < len(self.history) and not expand_only else None
        if cached is not None:
            # The cache belongs to ONE seeded, deterministic scenario. Check
            # regenerated legality and event/decision recall before reusing its
            # immutable views. No engine option or runtime graph is retained.
            if (cached.header != header or cached.prior_recall != self.recalls[actor]
                    or set(cached.descriptors) != set(descriptors)):
                raise ReplayDiverged(f"Cached prefix decision {self.cursor} diverged")
            if state is not None:
                PlayerObservationBuilder(state, request.player).observe_knowledge()
            address, views, information, bindings = (cached.address, cached.views,
                                                     cached.information, cached.bindings)
            self.recalls[actor] = information.recall
            self.simulator.projection_cache_hits += 1
            self.simulator.record_stage('cached_projection', started)
            return self._select(request, options, descriptors, address, views, bindings, information)
        # Sort only the fingerprint: regenerated enumeration order may differ,
        # but binding always uses the actual descriptor, never its list index.
        menu = sorted((d.data for d in descriptors), key=repr)
        digest = (structural_key(menu) if self.simulator.structured_records
                  else sha256(repr(menu).encode()).hexdigest())
        if existing_views is not None and self.simulator.structured_records:
            from game.ai.structured_values import payload
            view_data = tuple((player, payload(view).value.readonly) for player, view in existing_views)
        else:
            view_data = self._view_data(state, request.participants, request)
        started = self.simulator.record_stage('views', started, return_start=True)
        bindings = ()
        information = None
        prior_recall = self.recalls[actor]
        if self.simulator.track_information:
            projected = DecisionProjector(request, options, self.recalls[actor], view=dict(view_data)[actor],
                                         retain_view='structured' if self.simulator.structured_records else True, store=self.simulator.value_store,
                                         structured=self.simulator.structured_records,
                                         candidate_model=model.identity if model else None, menu_complete=self.candidate_complete,
                                         candidate_width=self.frontier.width if self.frontier else 0).build()
            actor_view = projected._view
            view_data = tuple((p, actor_view if p == actor else v) for p, v in view_data)
            descriptors_by_id = {id(option): descriptor for option, descriptor in zip(options, descriptors)}
            bindings = tuple((action, descriptors_by_id[id(option)]) for action, option in projected._bindings)
            self.recalls[actor] = projected.information.recall
            information = projected.information
        started = self.simulator.record_stage('player_projection', started, return_start=True)
        # Preserve the reference address encoding, including setup views' key
        # order. The acting setup view was sorted after projection previously.
        views = tuple((p, StructuredJSON(v, self.simulator.value_store) if self.simulator.structured_records
                       else json.dumps(v, sort_keys=(state is not None or
                       (self.simulator.track_information and p == actor)))) for p, v in view_data)
        address = DecisionAddress(type(request).__name__, request.player.idx,
                                  state.turn.number if state is not None else None,
                                  state.turn.phase.name if state is not None else None, digest,
                                  structural_key(views) if self.simulator.structured_records else sha256(repr(views).encode()).hexdigest())
        if self.simulator.structured_records and len(descriptors) == 1 and information is not None:
            address = DecisionAddress(address.kind, address.player, address.turn, address.phase,
                                      structural_key(descriptors[0].data), structural_key(information.current.structured))
        self.simulator.record_stage('views_and_address_storage', started)
        if information is not None and len(cache) < self.simulator.projection_cache_size:
            cache.setdefault(cache_key, _Projection(header, descriptors, address, views, prior_recall, information, bindings))
        if expand_only:
            return SimulationNode(self.history, address, tuple(sorted(descriptors, key=lambda d: repr(d.data))), False, False,
                self.phase_steps, views, self.simulator._identity, self.recall_snapshot(), bindings, information,
                _candidate_order=descriptors if self.frontier else ())
        return self._select(request, options, descriptors, address, views, bindings, information)

    def _forced_decision(self, request, option):
        started = perf_counter() if self.simulator.profile_advance else 0
        actor = request.player.idx
        prior_recall = self.recalls[actor]
        shared_characteristics = {}
        view = (decision_observation(request, shared_characteristics=shared_characteristics,
                projection_store=self.simulator.value_store) if request.state is not None else
                {'self': actor, 'setup': True, 'players': [{'id': p.idx, 'name': p.name} for p in request.participants]})
        evidence = getattr(request.player.controller, 'initial_search_evidence', None)
        if evidence is not None:
            view['initial_search_evidence'] = evidence
        model = getattr(self.simulator.policy_for, 'candidate_model', None)
        projected = DecisionProjector(request, (option,), self.recalls[actor], view=view,
                                     store=self.simulator.value_store, candidate_model=model.identity if model else None,
                                     candidate_width=self.frontier.width if self.frontier else 0).build()
        info = projected.information
        self.recalls[actor] = info.recall
        descriptor = ChoiceDescriptor.from_option(option)
        state = request.state
        address = DecisionAddress(type(request).__name__, actor,
            state.turn.number if state is not None else None,
            state.turn.phase.name if state is not None else None,
            structural_key(descriptor.data), structural_key(info.current.structured))
        # Future reconstruction can replay this exact deterministic prefix
        # without re-projecting it. A prefix replay consumes the cached action
        # immediately; it never publishes these actor-only views as a leaf.
        # Keep the same legality/header/recall checks as ordinary projections.
        cache = self.simulator._projections
        if len(cache) < self.simulator.projection_cache_size:
            key = self.history if model is None else (self.history, 1, True)
            header = (address.kind, actor, address.turn, address.phase)
            cache.setdefault(key, _Projection(header, (descriptor,), address,
                ((actor, info.current._json),), prior_recall, info,
                ((info.actions[0], descriptor),)))
        boundary = ForcedBoundary(self, request, address, descriptor, info, shared_characteristics)
        self.simulator.record_stage('forced_projection', started)
        self.simulator.native_forced_steps += 1
        chosen = self.follow(boundary)
        if chosen is None:
            self.node = boundary.materialize()
            raise _DecisionBoundary()
        if chosen != info.actions[0]:
            raise ReplayDiverged('Native forced callback changed the only legal action')
        if len(self.history) >= self.simulator.max_decisions:
            raise SimulationBudgetExceeded('Decision limit reached while following forced actions')
        self.history = (*self.history, RecordedChoice(address, descriptor, self.frontier.width if self.frontier else None))
        self.cursor += 1
        self.recalls[actor] = self.recalls[actor].append('action', chosen.structured)
        return DecisionResult(option)

    def _select(self, request, options, descriptors, address, views, bindings, information):
        actor = request.player.idx
        if self.cursor < len(self.history):
            recorded = self.history[self.cursor]
            if recorded.address != address or recorded.choice not in descriptors:
                raise ReplayDiverged(f"Decision {self.cursor} diverged: expected {recorded.address.kind}, got {address.kind}")
            if self.simulator.track_information:
                action = next(a for a, d in bindings if d == recorded.choice)
                self.recalls[actor] = self.recalls[actor].append("action", action.describe())
            self.cursor += 1
            return DecisionResult(options[descriptors.index(recorded.choice)])
        self.node = SimulationNode(self.history, address, tuple(sorted(descriptors, key=lambda d: repr(d.data))), False, False,
                                   self.phase_steps, views, self.simulator._identity, self.recall_snapshot(), bindings,
                                   information, _candidate_order=descriptors if self.frontier else ())
        if self.follow is not None:
            chosen = self.follow(self.node)
            if chosen is not None:
                if len(self.history) >= self.simulator.max_decisions:
                    raise SimulationBudgetExceeded("Decision limit reached while following actions")
                descriptor = self.node.bind_action(chosen)
                if self.frontier is not None:
                    options = tuple(self.frontier.options)
                    descriptors = tuple(ChoiceDescriptor.from_option(option) for option in options)
                    address = self.node.decision
                # Still inside the original controller call: no rule frame has
                # unwound, so this ordinary return resumes the real engine.
                self.history = (*self.history, RecordedChoice(address, descriptor,
                    self.frontier.width if self.frontier is not None else None))
                self.cursor += 1
                self.recalls[actor] = self.recalls[actor].append("action", chosen.structured)
                self.follow_started = True
                return DecisionResult(options[descriptors.index(descriptor)])
        raise _DecisionBoundary()


class SearchSimulator:
    """!
    @brief Advance/fork a fixed deterministic scenario without touching a live match.

    factory(controllers) must return a fresh object with state and loop, including
    fresh mutable rule callbacks and a private seeded RNG. It must use the supplied
    controllers even during setup, and perform no logging/network/file I/O. The
    built-in starter_scenario follows this contract. Arbitrary extension callbacks
    remain trusted engine code, not sandboxed code.
    """
    def __init__(self, factory, *, max_options=4096, max_decisions=10000,
                 max_phases=1000, policy_for=None, track_information=False, initial_recalls=(),
                 view_mode="full", observer=None, projection_cache_size=0, structured_records=False,
                 native_forced=True, profile_advance=False):
        for name, value in (("max_options", max_options), ("max_decisions", max_decisions), ("max_phases", max_phases)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.factory = factory
        if type(structured_records) is not bool:
            raise ValueError('structured_records must be boolean')
        self.structured_records = structured_records
        self.value_store = ValueStore()
        if type(native_forced) is not bool or type(profile_advance) is not bool:
            raise ValueError('Native forced/profile switches must be boolean')
        self.native_forced, self.profile_advance = native_forced, profile_advance
        self.stages = {}
        self.native_forced_steps = 0
        self._active_candidates = None
        self.candidate_widths = {}
        self.candidate_expansions = 0
        self.max_options, self.max_decisions, self.max_phases = max_options, max_decisions, max_phases
        self.policy_for = policy_for or (lambda request: None)
        self._identity = object()
        if type(track_information) is not bool:
            raise ValueError("track_information must be boolean")
        self.track_information = track_information
        if view_mode not in {"full", "search"}:
            raise ValueError("view_mode must be full or search")
        if view_mode == "search" and (not track_information or type(observer) is not int or observer not in (0, 1)):
            raise ValueError("Search views require information tracking and an explicit observer 0/1")
        self.view_mode, self.observer = view_mode, observer
        if type(projection_cache_size) is not int or projection_cache_size < 0:
            raise ValueError("projection_cache_size must be a nonnegative integer")
        if projection_cache_size and not track_information:
            raise ValueError("Projection caching requires information tracking")
        self.projection_cache_size = projection_cache_size
        self._projections = {}
        self.projection_cache_hits = 0
        # Detached menus only, scoped to this one deterministic sampled world.
        # Share the existing entry limit; no live State/request/generator survives.
        self._candidate_nodes = OrderedDict()
        self._candidate_cache_limit = projection_cache_size
        self.candidate_cache_hits = 0
        self.initial_recalls = tuple(initial_recalls)
        if (len(dict(self.initial_recalls)) != len(self.initial_recalls)
                or any(p not in (0, 1) or not isinstance(r, PlayerRecall) for p, r in self.initial_recalls)):
            raise ValueError("Initial recalls require distinct player 0/1 and immutable PlayerRecall values")

    def record_stage(self, name, started, *, return_start=False):
        """!
        @brief Accumulate optional simulator timing without changing search semantics.
        """
        if not started:
            return 0
        now = perf_counter()
        calls, seconds = self.stages.get(name, (0, 0))
        self.stages[name] = calls + 1, seconds + now - started
        return now if return_start else 0

    def start(self):
        """!
        @brief Initialize the owned scenario and expose its first supported boundary.
        """
        return self._replay(())

    def follow_actions(self, choose, *, node=None, coalesce_forced=False):
        """!
        @brief Run a trajectory in one fresh runtime, pausing when choose returns None.

        Trusted infrastructure receives each immutable node and returns an offered
        PlayerAction or None. This supports history initialization and search trajectories;
        it does not branch a suspended live State. At the boundary the runtime is
        still unwound/discarded as in start().
        The returned node retains a complete strict replay history for future advances.
        If node is supplied, its recorded prefix is replayed/validated first and the
        chooser is invoked only from that boundary onward.
        """
        if not self.track_information:
            raise ValueError("Enable track_information to follow player actions")
        if not callable(choose):
            raise TypeError("Supply a player-action chooser")
        if node is not None:
            self._check_node(node)
            if node.terminal or node.cutoff:
                raise ValueError("Cannot follow from a terminal or cutoff node")
            info = node.player_information()
            if info.candidate_model is not None:
                self.candidate_widths[node.history] = info.candidate_width
        return self._replay(() if node is None else node.history, follow=choose, coalesce_forced=coalesce_forced)

    def advance_until_choice(self, node, action, *, on_forced=None):
        """!
        @brief Advance in one owned engine through forced steps to a choice/end.

        on_forced is called at EVERY logical step before its execution. Return
        false to stop there, for example at a depth/time/work boundary. Events
        and actor recall are collected even when no public node is requested.
        """
        first = True
        def choose(current):
            nonlocal first
            if first:
                first = False
                return action
            if len(current.player_information().actions) != 1:
                return None
            if on_forced is not None and not on_forced(current):
                return None
            return current.player_information().actions[0]
        return self.follow_actions(choose, node=node, coalesce_forced=True)

    def fork(self, node):
        """!
        @brief Validate and reuse an immutable boundary belonging to this simulator.
        """
        self._check_node(node)
        if node.decision is not None and self.track_information and node.player_information().candidate_model is not None:
            self.candidate_widths[node.history] = node.player_information().candidate_width
        branch = self._replay(node.history)
        if branch != node:
            raise ReplayDiverged("Reconstructed node differs from its branch point")
        return branch

    def advance(self, node, choice):
        """!
        @brief Execute or reuse one transition under configured replay and budget checks.
        """
        self._check_node(node)
        if node.decision is None:
            raise ValueError("Cannot choose an action at a terminal or cutoff node")
        if not isinstance(choice, ChoiceDescriptor) or choice not in node.choices:
            raise ValueError("Choose a descriptor offered at this decision")
        if len(node.history) >= self.max_decisions:
            raise SimulationBudgetExceeded("Decision limit reached")
        width = node.player_information().candidate_width if self.track_information else 0
        return self._replay((*node.history, RecordedChoice(node.decision, choice, width or None)))

    def advance_action(self, node, action):
        """!
        @brief Bind a portable visible choice to this world's regenerated legal action.
        """
        self._check_node(node)
        return self.advance(node, node.bind_action(action))

    def expand_candidates(self, node, limit):
        """!
        @brief Resume the native frontier to admit a wider candidate prefix.
        """
        self._check_node(node)
        if node.player_information().candidate_model is None:
            raise ValueError('This node has a complete native menu, not a candidate frontier')
        if not node.player_information().candidate_width < limit <= self.max_options:
            raise SimulationBudgetExceeded('Candidate expansion reached its option budget')
        active = self._active_candidates
        if active is not None and active[0].history == node.history:
            self.candidate_expansions += 1
            expanded = active[0].expand_candidates(active[1], limit)
        else:
            cached = self._candidate_nodes.get(node.history) if self._candidate_cache_limit else None
            if cached is not None:
                old, new = node.player_information(), cached.player_information()
                if (cached.phase_steps == node.phase_steps and old.fast_key == new.fast_key
                        and set(node.choices) <= set(cached.choices)
                        and (not node._candidate_order or
                             cached._candidate_order[:len(node._candidate_order)] == node._candidate_order)
                        and (new.candidate_width >= limit or
                             (new.menu_complete and len(cached.choices) <= limit))):
                    expanded = self._candidate_prefix(cached, limit)
                    self.candidate_widths[node.history] = limit
                    self.candidate_cache_hits += 1
                    self._candidate_nodes.move_to_end(node.history)
                    return expanded
            self.candidate_expansions += 1
            model = getattr(self.policy_for, 'candidate_model', None)
            # Amortize detached native generation in geometric batches. Only
            # the requested prefix is admitted; extra proposals stay private.
            reserve = (min(self.max_options, 1 << (limit-1).bit_length())
                       if self._candidate_cache_limit and getattr(model, 'native_ordering', False) else limit)
            self.candidate_widths[node.history] = reserve
            expanded = self._replay(node.history)
        self._remember_candidate_menu(expanded)
        self.candidate_widths[node.history] = limit
        return self._candidate_prefix(expanded, limit)

    def _remember_candidate_menu(self, node):
        if not self._candidate_cache_limit:
            return
        previous = self._candidate_nodes.get(node.history)
        if (previous is None or
                previous.player_information().candidate_width < node.player_information().candidate_width):
            self._candidate_nodes[node.history] = node
        self._candidate_nodes.move_to_end(node.history)
        while len(self._candidate_nodes) > self._candidate_cache_limit:
            self._candidate_nodes.popitem(last=False)

    def _candidate_prefix(self, node, limit):
        """!
        @brief Admit an exact prefix of detached proposals, with strict replay keys.
        """
        from dataclasses import replace
        info = node.player_information()
        if info.candidate_width == limit:
            return node
        order = node._candidate_order or node.choices
        descriptors = order[:limit]
        admitted = set(descriptors)
        bindings = tuple((a, d) for a, d in node._action_bindings if d in admitted)
        information = replace(info, actions=tuple(a for a, _ in bindings), candidate_width=limit,
                              menu_complete=info.menu_complete and len(order) <= limit)
        menu = sorted((d.data for d in descriptors), key=repr)
        digest = structural_key(menu) if self.structured_records else sha256(repr(menu).encode()).hexdigest()
        observation_digest = (structural_key(node._views) if self.structured_records
                              else sha256(repr(node._views).encode()).hexdigest())
        if self.structured_records and len(descriptors) == 1:
            digest = structural_key(descriptors[0].data)
            observation_digest = structural_key(information.current.structured)
        address = replace(node.decision, menu_digest=digest, observation_digest=observation_digest)
        return replace(node, decision=address, choices=tuple(sorted(descriptors, key=lambda d: repr(d.data))),
                       _action_bindings=bindings, _projected_information=information, _candidate_order=descriptors)

    def _check_node(self, node):
        if not isinstance(node, SimulationNode) or node._owner is not self._identity:
            raise ValueError("Node belongs to another simulator")

    def _replay(self, history, *, follow=None, coalesce_forced=False):
        replay = self._make_replay(history)
        replay.follow = follow
        replay.coalesce_forced = coalesce_forced and self.native_forced
        return self._run_replay(replay, self.factory)

    def _run_replay(self, replay, factory):
        """!
        @brief Shared runner; continuation backends supply an isolated factory.
        """
        controllers = (_ReplayController(replay), _ReplayController(replay))
        try:
            replay.in_factory = True
            game = factory(controllers)
            replay.in_factory = False
            if tuple(p.controller for p in game.state.players) != controllers:
                raise ValueError("Scenario must install the supplied simulation controllers")
            while not game.state.is_game_over and replay.phase_steps < self.max_phases:
                game.loop.step(game.state)
                replay.phase_steps += 1
            if replay.cursor != len(replay.history):
                raise ReplayDiverged("Scenario ended before consuming the recorded prefix")
            terminal = game.state.is_game_over
            return SimulationNode(replay.history, None, (), terminal, not terminal, replay.phase_steps,
                                  replay.views(game.state, game.state.players), self._identity, replay.recall_snapshot())
        except _DecisionBoundary:
            if replay.node is None:
                raise ReplayDiverged("Unexpected decision boundary")
            return replay.node
        except TurnHistoryUnavailable as exc:
            raise SimulationHistoryUnavailable(str(exc)) from exc

    def _make_replay(self, history):
        return _Replay(self, history)


def starter_scenario(*, seed=1, colors=("white", "red")):
    """!
    @brief Fixed full-rules starter game with a fresh engine/RNG on every replay.
    """
    if type(seed) is not int:
        raise ValueError("A deterministic integer seed is required")
    colors = tuple(colors)
    def build(controllers):
        from game.console.demo_game import create_starter_demo_game
        return create_starter_demo_game(controllers, seed=seed, colors=colors)
    return build
