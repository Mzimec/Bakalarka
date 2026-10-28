"""Trusted bridge: public runtime + declared deck copies -> current-root worlds.

Hidden runtime card objects, their order, controllers and match RNG are never
copied. Unknown slots are filled from the explicitly granted deck lists. This
first prior is uniform without replacement, conditioned on known locations;
it does not yet weight cards by the opponent's past action likelihood.
"""
from dataclasses import dataclass
from random import Random

from game.ai.runtime_clone import RuntimeClone, UnsupportedSnapshot, snapshot_failure_trace
from game.ai.search_simulator import SearchSimulator
from game.ai.library_positions import LibraryPositions
from game.enums import ZoneType, TurnPhase


@dataclass
class _Game:
    """!
    @brief Pair an owned native state with its executable game loop.
    @var state State retained by this record.
    @var loop Loop retained by this record.
    """
    state: object
    loop: object


def _register_cards(state, cards, references, introductions, cursor, next_command_id):
    from game.game_state.registers.card_register import CardRegister
    from game.game_state.registers.player_register import PlayerRegister
    state.card_register = register = CardRegister(state)
    state.player_register = PlayerRegister(state)
    # Do not refresh static effects against a half-populated registry: that
    # would detach/recreate sources and change their timestamp ordering.
    state._refreshing_effects = True
    # Seed command references before registration so public action identities
    # and references to cards in stack/LKI remain unchanged.
    register._assigned_command_ids = {id(c): (c, references[c.key]) for c in cards}
    register._reference_owners = {references[c.key]: c for c in cards}
    register._next_command_id = next_command_id
    register._introduction_sequence = cursor
    register._introductions = {id(c): (c, introductions[c.key]) for c in cards}
    # Query iteration follows runtime IDs. Preserve public order as well as
    # command references; fill remaining slots without consulting hidden IDs.
    occupied = {c.runtime_id for c in cards if c.runtime_id is not None}
    next_id = 0
    for card in cards:
        if card.runtime_id is None:
            while next_id in occupied:
                next_id += 1
            card.runtime_id = next_id
            occupied.add(next_id)
    ordered = sorted(cards, key=lambda c: c.runtime_id)
    register.storage._id_to_obj = [None] * (max(occupied, default=-1) + 1)
    register.storage._free_ids = [c.runtime_id for c in reversed(ordered)]
    for player in state.players:
        player.runtime_id = None
        state.player_register.register(player)
    for card in ordered:
        card.runtime_id = None
        card._game_state = state
        register.register(card)
    state._effects_dirty = True
    state._refreshing_effects = False
    state.synchronise_registers()


class CurrentPositionWorlds:
    """!
    @brief Capture at native priority or supported combat-declaration boundaries.

    The agent calls prepare with its local request; search/policies only receive
    detached observations and regenerated options. Other root continuations return
    an explicit unsupported reason. Nested future choices work through suffix
    replay from this boundary, rather than replaying any real past decisions.
    """
    def __init__(self, origin, *, checkpoint_limit=0, checkpoint_interval=4,
                 checkpoint_continuous=True, checkpoint_min_visits=2, reuse_world=False,
                 continuation_attempts=4, continuation_advances=256,
                 history_exploration=0.2, **simulator_options):
        if type(reuse_world) is not bool:
            raise ValueError("reuse_world must be boolean")
        self.reuse_world = reuse_world
        if type(checkpoint_limit) is not int or checkpoint_limit < 0:
            raise ValueError("checkpoint_limit must be a nonnegative integer")
        self.checkpoint_limit = checkpoint_limit
        if type(checkpoint_interval) is not int or checkpoint_interval<1:
            raise ValueError('checkpoint_interval must be a positive integer')
        if type(checkpoint_continuous) is not bool or type(checkpoint_min_visits) is not int or checkpoint_min_visits<1:
            raise ValueError('Invalid continuous checkpoint settings')
        self.checkpoint_options = dict(checkpoint_interval=checkpoint_interval,
            checkpoint_continuous=checkpoint_continuous,checkpoint_min_visits=checkpoint_min_visits)
        self.origin, self.options = origin, simulator_options
        self.unsupported_reason = None
        self.unsupported_detail = None
        for value in (continuation_attempts, continuation_advances):
            if type(value) is not int or value < 1:
                raise ValueError("Continuation sampling limits must be positive integers")
        self.continuation_attempts, self.continuation_advances = continuation_attempts, continuation_advances
        if type(history_exploration) not in (int, float) or not 0 < history_exploration <= 1:
            raise ValueError("history_exploration must be in (0, 1]")
        self.history_exploration = history_exploration
        self.reset()

    def reset(self):
        """!
        @brief Discard session-owned mutable state before preparing a new search context.
        """
        self._anchor = None
        self.preparation = None

    def remember_boundary(self, request, information, prior_recall, knowledge):
        """!
        @brief Retain at most one safe anchor per phase, also for forced windows.

        Capturing the already sanitized boundary permits nested trigger, cost,
        replacement and cleanup prompts to replay a short *observed* suffix.
        No native Python frame or inaccessible opponent choice is retained.
        """
        from game.ai.decision_maker import PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest
        from game.game_actions.generation.decision_abstraction.combat_frames import CombatNextRequest
        if not isinstance(request, (PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest, CombatNextRequest)):
            return
        if isinstance(request, PriorityDecisionRequest) and not self._needs_forced_anchor(request):
            return
        marker = (request.state.turn.number, request.state.turn.phase)
        if self._anchor is not None and self._anchor[3] == marker:
            return
        self.prepare(request, information, prior_recall, knowledge, _allow_continuation=False)

    @staticmethod
    def _needs_forced_anchor(request):
        """!
        @brief Do not copy every empty pass window just to retain a continuation.

        Native upcoming damage/cleanup/stack processing can introduce nested
        choices. Phase-trigger metadata is only used as a conservative filter;
        unknown conditions stay eligible, and private definitions are not read.
        """
        state = request.state
        if not state.stack.is_empty() or state.turn.phase == TurnPhase.END_STEP:
            return True
        if (state.turn.phase in {TurnPhase.DECLARE_BLOCKERS, TurnPhase.FIRST_COMBAT_DAMAGE}
                and state.combat.active and state.combat.attackers):
            return True
        from types import SimpleNamespace
        from game.game_actions.triggers.trigger_roster import capture_native_roster, event_key_possible
        from game.enums import ZoneType
        keys = ["phase_started"]
        if state.turn.phase == TurnPhase.UPKEEP:
            keys.extend(("card_drawn", "card_moved"))
        for binding in capture_native_roster(state):
            source = binding.source
            if source is None or not callable(getattr(source, "get_zone", None)):
                return True
            if source.get_zone() in {ZoneType.DECK, ZoneType.HAND} and source.owner is not request.player:
                continue
            if any(event_key_possible(binding.definition, SimpleNamespace(key=key)) for key in keys):
                return True
        return False

    def _continuation(self, request, information):
        cause, detail = self.unsupported_reason, self.unsupported_detail
        if self._anchor is None or information is None:
            return None
        factory, token, observer, _, snapshot = self._anchor
        if request.state.knowledge_token is not token or request.player.idx != observer:
            return None
        conditioned_snapshot = self._condition_inspection(snapshot, information)
        if conditioned_snapshot is not snapshot:
            factory = self._factory(conditioned_snapshot)
        from game.ai.history_world_provider import HistoryWorldProvider, ExploratoryHistoryPolicy
        from game.ai.observation_policy import ObservationOpponentPolicy
        conditioned = HistoryWorldProvider(factory,
            opponent_weights=ExploratoryHistoryPolicy(ObservationOpponentPolicy(), exploration=self.history_exploration),
            max_attempts=self.continuation_attempts, max_prefix_advances=self.continuation_advances)
        self.unsupported_reason = None
        self.unsupported_detail = None
        self.preparation = {"route": "continuation", "cause": cause, "detail": detail,
                            "anchor_turn": self._anchor[3][0], "anchor_phase": self._anchor[3][1].name}
        return conditioned(information)

    @staticmethod
    def _condition_inspection(snapshot, information):
        """!
        @brief Pin an observed scry prefix only when the anchor library is untouched.

        A necessary condition of every matching continuation can be conditioned
        before rejection. Never inspect the live library or force an opponent
        action. Other library mutations retain ordinary joint rejection.
        """
        if information.current.request_kind != "ScryRequest":
            return snapshot
        observer = snapshot.observer
        suffix = information.recall.entries[len(snapshot.prior_recall.entries):]
        for entry in suffix[:-1]:
            data = entry.describe()
            if entry.kind == "action" and data.get("kind") in {"ScryOption", "MulliganOption", "MulliganBottomOption"}:
                return snapshot
            if entry.kind == "event" and data.get("owner") == observer and (
                    data.get("kind") in {"library_shuffled", "card_drawn"}
                    or "DECK" in (data.get("from_zone"), data.get("to_zone"))):
                return snapshot
        revealed = information.current.observation().get("decision_context", {}).get("revealed_cards", ())
        if not revealed:
            return snapshot
        rows = {ref: name for _, name, ref in snapshot.remaining[observer]}
        positions = list(snapshot.library_positions.slots[observer])
        names, revisions = dict(snapshot.library_positions.names), dict(snapshot.library_positions.revisions)
        if len(revealed) > len(positions):
            return snapshot
        for index, card in enumerate(revealed):
            ref, name = card.get("id"), card.get("name")
            if rows.get(ref) != name or positions[index] not in (None, ref):
                return snapshot
            if ref in positions and positions.index(ref) != index:
                return snapshot
            positions[index] = ref
            names[ref] = name
            if type(card.get("zone_revision")) is int:
                revisions[ref] = card["zone_revision"]
        from copy import copy
        result = copy(snapshot)
        slots = list(snapshot.library_positions.slots)
        slots[observer] = tuple(positions)
        result.library_positions = LibraryPositions(tuple(slots), tuple(sorted(names.items())), tuple(sorted(revisions.items())))
        return result

    def _factory(self, snapshot):
        # Transport only the sanitized root and plain simulator configuration.
        # Capturing self would also transport the provider's other live anchors.
        simulator_options = dict(self.options)
        reuse_world, checkpoint_limit = self.reuse_world, self.checkpoint_limit
        checkpoint_options = self.checkpoint_options
        def factory(seed):
            options = dict(track_information=True,
                initial_recalls=((snapshot.observer, snapshot.prior_recall),), **simulator_options)
            if options.get("view_mode") == "search":
                options["observer"] = snapshot.observer
            build = _PreparedWorld(snapshot, seed, reuse=reuse_world)
            if checkpoint_limit:
                from game.ai.checkpoint_simulator import CheckpointSimulator
                return CheckpointSimulator(build, checkpoint_limit=checkpoint_limit, **checkpoint_options, **options)
            return SearchSimulator(build, **options)
        return factory

    def prepare(self, request, information, prior_recall, knowledge, *, _allow_continuation=True):
        """!
        @brief Prepare compatible sampled-world inputs for the current decision.
        """
        from game.ai.decision_maker import PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest
        from game.game_loop.continuations import is_combat_damage_order
        from game.game_actions.generation.decision_abstraction.combat_frames import CombatNextRequest
        self.unsupported_reason = None
        self.preparation = {"route": "unavailable"}
        self.unsupported_detail = {"request": type(request).__name__,
                                   "phase": request.state.turn.phase.name if request.state is not None else None}
        if (not isinstance(request, (PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest, CombatNextRequest))
                and not is_combat_damage_order(request)):
            self.unsupported_reason = "unsupported_root_continuation"
            return self._continuation(request, information) if _allow_continuation else None
        if request.state.turn.phase in (TurnPhase.UNTAP, TurnPhase.CLEANUP):
            self.unsupported_reason = "unsupported_phase_continuation"
            return self._continuation(request, information) if _allow_continuation else None
        try:
            snapshot = _Snapshot(request, information, prior_recall, knowledge, self.origin)
        except UnsupportedSnapshot as exc:
            self.unsupported_reason = f"unsupported_snapshot: {exc}"
            self.unsupported_detail["reference_path"] = snapshot_failure_trace(exc)
            return self._continuation(request, information) if _allow_continuation else None
        self.unsupported_detail = None
        self.preparation = {"route": "current"}
        factory = self._factory(snapshot)
        self._anchor = (factory, request.state.knowledge_token, request.player.idx,
                        (request.state.turn.number, request.state.turn.phase), snapshot)
        return factory


class _PreparedWorld:
    """!
    @brief Materialize once, then restore an isolated native ownership graph.

    The retained state is captured before any rule continuation or decision. It is
    never executed. Both the first branch and every subsequent replay receive a
    copy, so even failures cannot poison later branches. No live hidden objects
    are introduced: the template comes from the existing visibility-safe sampler.
    """
    def __init__(self, snapshot, seed, *, reuse=True, cow_indexes=False):
        self.snapshot, self.seed = snapshot, seed
        self.reuse = reuse
        if cow_indexes and not reuse:
            raise ValueError("COW index experiment requires a reusable sampled world")
        self.cow_indexes = cow_indexes
        self.state = self.knowledge = None
        self.immutable_values = {}
        self.dictionary_plans = None
        self.materializations = self.restores = 0

    def materialize(self, controllers):
        """!
        @brief Restore an isolated copy of the sanitized sampled root template.
        """
        from game.ai.simulation_world import copy_world, native_loop, immutable_world_values
        if not self.reuse:
            game = self.snapshot.materialize(controllers, self.seed)
            self.materializations += 1
            return game
        if self.state is None:
            game = self.snapshot.materialize(controllers, self.seed)
            memories = tuple(p.controller.knowledge for p in game.state.players)
            for player in game.state.players:
                player.controller = None
            self.state, self.knowledge = game.state, memories
            if self.cow_indexes:
                from game.ai.simulation_world import prepare_cow_indexes
                prepare_cow_indexes(self.state)
            self.immutable_values = immutable_world_values(self.state)
            from game.ai.runtime_clone import DictionaryCopyPlans
            self.dictionary_plans = DictionaryCopyPlans(self.immutable_values)
            self.materializations += 1
        try:
            state, memories = copy_world(self.state, self.knowledge,
                                        immutable_values=self.immutable_values,
                                        dictionary_plans=self.dictionary_plans)
        finally:
            self.dictionary_plans.collecting = False
        for player, controller, memory in zip(state.players, controllers, memories):
            player.controller = controller
            controller.knowledge = memory
        self.restores += 1
        return _Game(state, native_loop())

    def __call__(self, controllers):
        game = self.materialize(controllers)
        return self.snapshot.resume(game)


class _Snapshot:
    """!
    @brief Sanitized recipe for reconstructing compatible current-position worlds.
    """
    def __getstate__(self):
        # Copy recipes contain process-local id() keys; they are not transport
        # data. Rebuild them against the deserialized, visibility-filtered graph.
        return {key: value for key, value in vars(self).items()
                if key not in ('immutable_values', 'dictionary_plans')}

    def __setstate__(self, values):
        self.__dict__.update(values)
        self._prepare_copy()

    def __init__(self, request, information, prior_recall, knowledge, origin):
        from game.game_state.battlefield import CardCollection
        from game.game_state.card_snapshot_cache import CardSnapshotCache
        from game.mana.source_cache import StaticManaSourceCache
        state, observer = request.state, request.player.idx
        from game.ai.decision_maker import PriorityDecisionRequest, DeclareAttackersRequest
        from game.game_actions.generation.decision_abstraction.combat_frames import CombatNextRequest
        from game.game_loop.continuations import CombatDeclarationBoundary, is_combat_damage_order
        self.combat_batch_root = is_combat_damage_order(request)
        self.combat_root = not isinstance(request, PriorityDecisionRequest)
        if self.combat_batch_root:
            pass  # Validated public batch frame includes deferred phase triggers.
        elif self.combat_root:
            boundary = getattr(state, "combat_declaration_boundary", None)
            attack = (isinstance(request, DeclareAttackersRequest) or
                      type(request) is CombatNextRequest and request.frame.kind == 'attackers')
            phase = (TurnPhase.DECLARE_ATTACKERS if attack
                     else TurnPhase.DECLARE_BLOCKERS)
            if (type(request) is CombatNextRequest
                    and getattr(state, 'combat_decision_frame', None) is not request.frame):
                raise UnsupportedSnapshot('Missing native combat construction frame')
            if (not isinstance(boundary, CombatDeclarationBoundary) or boundary.phase != phase
                    or state.turn.phase != phase or boundary.player != observer or len(state.players) != 2):
                raise UnsupportedSnapshot("Missing native combat declaration boundary")
        elif state.priority.current_player is not request.player:
            raise UnsupportedSnapshot("Priority belongs to another player")
        if state._refreshing_effects or state._deferred_events:
            raise UnsupportedSnapshot("Unsettled priority boundary")
        self.observer, self.prior_recall = observer, prior_recall
        self.knowledge = RuntimeClone().copy(knowledge)
        self.catalog_factory = origin.catalog_factory
        self.sizes = tuple((len(p.hand), len(p.deck)) for p in state.players)
        self.library_positions = LibraryPositions.from_recall(prior_recall, observer,
                                                              tuple(size[1] for size in self.sizes))
        visible = tuple(c for p in state.players for c in p.get_cards()
                        if c.get_zone() not in {ZoneType.DECK, ZoneType.HAND}
                        or (p.idx == observer and c.get_zone() == ZoneType.HAND))
        if any(c.state.face_down for c in visible):
            raise UnsupportedSnapshot("Face-down public cards require an identity adapter")
        if any(getattr(c, "_entry_copy_restore", None) for c in visible):
            raise UnsupportedSnapshot("Copied printed identities require a provenance adapter")
        from game.ai.runtime_clone import reject_unknown_history_sources
        from game.game_state.state import State
        from game.game_state.turn_history import TurnHistory
        history = state.turn_history if type(state) is State else None
        projected_history = history.for_simulation() if type(history) is TurnHistory else None
        if projected_history is None:
            reject_unknown_history_sources(state, visible, state.players[observer])
        self.visible_keys = {c.key for c in visible}
        self.references = {c.key: c.command_id for c in visible}
        self.introductions = {c.key: state.card_register._introductions[id(c)][1] for c in visible}
        self.registration_cursor = state.card_register.registration_cursor
        self.next_command_id = state.card_register._next_command_id
        # create_game assigns original copy keys/command IDs in declared order,
        # before any shuffle. Reconstruct that PUBLIC recipe, never inspect an
        # unknown runtime card to infer either its identity or its location.
        self.remaining = [[], []]
        reference = 0
        visible_by_key = {c.key: c for c in visible}
        for owner, deck in enumerate(origin.decks):
            number = 0
            for name, count in deck.cards:
                for _ in range(count):
                    key = f"p{owner}-card{number}"
                    reference += 1
                    number += 1
                    if key in visible_by_key:
                        c = visible_by_key[key]
                        if c.definition.name != name or c.command_id != f"c{reference}":
                            raise UnsupportedSnapshot("Position differs from the declared native setup")
                    else:
                        self.remaining[owner].append((key, name, f"c{reference}"))
                        self.references[key] = f"c{reference}"
                        self.introductions[key] = reference
        original_keys = {f"p{p}-card{i}" for p, d in enumerate(origin.decks) for i in range(d.size)}
        if any(not c.is_token and c.key not in original_keys for c in visible):
            raise UnsupportedSnapshot("Created non-deck cards require a provenance adapter")
        for owner, rows in enumerate(self.remaining):
            hand, library = self.sizes[owner]
            if len(rows) != library + (hand if owner != observer else 0):
                raise UnsupportedSnapshot("Remaining declared copies do not match hidden zone sizes")
        self.known_locations = information.current.observation()["knowledge"]["known_locations"]
        for owner, rows in enumerate(self.remaining):
            by_ref = {row[2]: row for row in rows}
            for ref in self.library_positions.slots[owner]:
                if ref is not None and (ref not in by_ref
                        or by_ref[ref][1] != dict(self.library_positions.names).get(ref)):
                    raise UnsupportedSnapshot("Known library identity disagrees with remaining copies")
            known_hand = set()
            known_library = {ref for ref in self.library_positions.slots[owner] if ref is not None}
            for fact in self.known_locations:
                if fact["owner"] != owner or fact["card_id"] not in by_ref:
                    continue
                if by_ref[fact["card_id"]][1] != fact["name"]:
                    raise UnsupportedSnapshot("Known identity disagrees with declared copies")
                if fact["zone"] == "HAND":
                    known_hand.add(fact["card_id"])
                elif fact["zone"] == "DECK":
                    known_library.add(fact["card_id"])
            hand_slots = self.sizes[owner][0] if owner != observer else 0
            if (known_hand & known_library or len(known_hand) > hand_slots
                    or len(known_library) > len(rows) - hand_slots):
                raise UnsupportedSnapshot("Known locations do not fit hidden zone sizes")
        replacements = {id(p.controller): None for p in state.players}
        from game.game_actions.triggers.trigger_registry import NativeTriggerRegistry
        replacements.update({id(state.card_register): None, id(state.player_register): None,
                             id(state._card_snapshots): CardSnapshotCache(),
                             id(state._mana_source_cache): StaticManaSourceCache()})
        replacements[id(state._trigger_registry)] = NativeTriggerRegistry()
        for p in state.players:
            replacements[id(p.deck)] = CardCollection()
            if p.idx != observer:
                replacements[id(p.hand)] = CardCollection()
        if hasattr(state, "rng"):
            replacements[id(state.rng)] = Random(0)
        # Scry's native event history can retain references to inspected cards.
        # Reconstruct those identities from PRIVATE RECALL, never copy their
        # current hidden runtime data. The same objects later fill sampled slots.
        known_names = dict(self.library_positions.names)
        known_revisions = dict(self.library_positions.revisions)
        remaining_by_ref = {row[2]: row for row in self.remaining[observer]}
        catalog = self.catalog_factory()
        def known_card(card):
            ref = getattr(card, "command_id", None)
            row = remaining_by_ref.get(ref)
            if row is None or known_names.get(ref) != row[1] or ref not in known_revisions:
                return None
            from game.game_state import Card
            fresh = Card(catalog[row[1]], state.players[observer], key=row[0])
            fresh.command_id = ref
            fresh.zone_revision = known_revisions[ref]
            fresh._game_state = state
            return fresh
        clone = RuntimeClone(replacements.items(), allowed_cards={id(c) for c in visible},
                             known_card_factory=known_card)
        from game.ai.snapshot_effects import project_hidden_static_effects, normalize_effect_order
        from game.game_state.state import State
        projected, removed = project_hidden_static_effects(state, set(visible)) if type(state) is State else (None, ())
        if projected is None and projected_history is None:
            self.state = clone.copy(state)
        else:
            # Copy a field projection while mapping all back-references to the
            # same new State. Never mutate the live manager/cards or copy a
            # hidden source merely because its derived static binding exists.
            fields = dict(vars(state))
            if projected_history is not None:
                fields["_turn_history"] = projected_history
            if projected is not None:
                fields["_cont_effect_manager"] = projected
                fields["_static_effect_keys"] = state._static_effect_keys - removed
                fields["_continuous_application_signature"] = None
                fields["_continuous_graph_signature"] = None
            self.state = object.__new__(State)
            clone.memo[id(state)] = self.state
            self.state.__dict__.update(clone.copy(fields))
            if projected is not None:
                normalize_effect_order(self.state)
        self.state._sampled_known_cards = clone.reconstructed_cards
        if hasattr(self.state, "setup_seed"):
            self.state.setup_seed = None
        self._prepare_copy()

    def _prepare_copy(self):
        from game.ai.simulation_world import immutable_world_values
        from game.ai.runtime_clone import DictionaryCopyPlans
        # This smaller root omits hidden runtime cards and populated registers.
        # Prepare its copy metadata once for all sampled worlds/replays.
        self.immutable_values = immutable_world_values(self.state)
        self.dictionary_plans = DictionaryCopyPlans(self.immutable_values)

    def copy_state(self, *, fast_copy=False):
        """!
        @brief Restore the never-executed visibility-filtered root ownership graph.
        """
        # Warm dictionary recipes already dominate this small graph; the
        # specialized copier showed no benefit here. Retain it for A/B trials.
        from game.ai.simulation_clone import SimulationClone
        copier = SimulationClone if fast_copy else RuntimeClone
        try:
            return copier(self.immutable_values,
                          dictionary_plans=self.dictionary_plans).copy(self.state)
        finally:
            if self.dictionary_plans is not None:
                self.dictionary_plans.collecting = False

    def materialize(self, controllers, seed):
        """!
        @brief Create a private world; only trusted simulation infrastructure calls this.
        """
        from game.game_state import Card
        from game.ai.simulation_world import rebind_replacement_instances, native_loop
        state = self.copy_state()
        rebind_replacement_instances(state)
        rng = Random(seed)
        state.rng = Random(seed ^ 0x918E)
        catalog = self.catalog_factory()
        cards = [c for p in state.players for c in p.get_cards()]
        known_revisions = dict(self.library_positions.revisions)
        for owner, player in enumerate(state.players):
            player.controller = controllers[owner]
            rows = list(self.remaining[owner])
            by_ref = {row[2]: row for row in rows}
            known_hand, known_library = [], []
            for fact in self.known_locations:
                if fact["owner"] != owner or fact["card_id"] not in by_ref:
                    continue
                row = by_ref[fact["card_id"]]
                if row[1] != fact["name"]:
                    raise UnsupportedSnapshot("Known identity disagrees with declared copies")
                if fact["zone"] == "HAND":
                    known_hand.append(row)
                elif fact["zone"] == "DECK":
                    known_library.append(row)
            for ref in self.library_positions.slots[owner]:
                if ref is not None and by_ref[ref] not in known_library:
                    known_library.append(by_ref[ref])
            fixed = set(known_hand + known_library)
            unknown = [row for row in rows if row not in fixed]
            rng.shuffle(unknown)
            slots = (self.sizes[owner][0] if owner != self.observer else 0) - len(known_hand)
            if not 0 <= slots <= len(unknown):
                raise UnsupportedSnapshot("Known locations do not fit the current hand")
            hand = known_hand + unknown[:slots]
            library = known_library + unknown[slots:]
            rng.shuffle(library)
            positions = self.library_positions.slots[owner]
            pinned = {ref for ref in positions if ref is not None}
            free = iter(row for row in library if row[2] not in pinned)
            # Evidence is top-first; the engine stores the top last.
            library = list(reversed([by_ref[ref] if ref is not None else next(free) for ref in positions]))
            for zone, entries in ((ZoneType.HAND, hand), (ZoneType.DECK, library)):
                for key, name, reference in entries:
                    card = state._sampled_known_cards.pop(key, None)
                    if card is None:
                        card = Card(catalog[name], player, key=key)
                        if reference in known_revisions:
                            card.zone_revision = known_revisions[reference]
                    card.state.zone_info.zone = zone
                    card.command_id = reference
                    # Populate without running zone changes or emitting events.
                    player._zone_map[zone].append(card)
                    cards.append(card)
        if state._sampled_known_cards:
            raise UnsupportedSnapshot("Reconstructed private references are outside the sampled zones")
        del state._sampled_known_cards
        _register_cards(state, cards, self.references, self.introductions,
                        self.registration_cursor, self.next_command_id)
        for owner, controller in enumerate(controllers):
            controller.knowledge.bind(state.knowledge_token, owner)
        root_controller = controllers[self.observer]
        root_controller.knowledge = RuntimeClone().copy(self.knowledge)
        root_controller.knowledge._game_token = state.knowledge_token
        # The opponent starts with its own sampled hand/current public view;
        # no live opponent private memory is imported.
        return _Game(state, native_loop())

    def build(self, controllers, seed):
        """!
        @brief Materialize a sanitized world and resume its supported decision continuation.
        """
        game = self.materialize(controllers, seed)
        return self.resume(game)

    def resume(self, game):
        """!
        @brief Resume a reconstructed world at its supported decision continuation.
        """
        if self.combat_batch_root:
            game.loop.resume_combat_damage_batch(game.state)
        elif self.combat_root:
            game.loop.resume_combat_declaration(game.state)
        else:
            game.loop.resume_priority(game.state)
        return game
