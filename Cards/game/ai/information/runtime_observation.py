"""State-owned visible readers and detached native decision products.

RuntimeObservation is a window onto one world. Its reusable card contracts never
enter the tree. A synchronous lease reads current visibility and characteristics;
the native search retains typed identities, ratings and horizon facts only.
Full historical JSON export is intentionally unavailable for identity records.
"""
from collections import Counter, OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field

from game.ai.information.observation import PlayerObservationBuilder
from game.ai.information.observation_characteristics import native_characteristics
from game.ai.information.observation_projection import ProjectedCardRow
from game.ai.information.structured_values import (
    DigestReference, StructuredJSON, RecallDigest, structural_digest,
)
from game.enums import ZoneType
from game.stat_type import STAT_STATIC_ABILITIES
from game.ai.information.visible_effects import (
    restriction_keywords, effect_features as _native_effect_features,
)
from game.ai.information.runtime_digest import (
    DigestNode, DigestReader, DigestSeedBank, RootDigestSeed, NativeSeed, card_base_parts,
    native_card_inputs, native_card_digest, native_readers,
)


_session = ContextVar('runtime_observation_session', default=None)
_NATIVE_RESTRICTION_READ = restriction_keywords
_UNREAD_NATIVE_INPUTS = object()
_NATIVE_RULE_RENDERER_TOKEN = object()
_NATIVE_RULE_RENDERERS = tuple((fn, fn.__code__, fn.__defaults__, fn.__kwdefaults__) for fn in (
    PlayerObservationBuilder.ability, PlayerObservationBuilder._ability,
    PlayerObservationBuilder.describe, PlayerObservationBuilder._describe,
    _native_effect_features, restriction_keywords))


class SnapshotUnavailable(RuntimeError):
    """A detached identity is not a historical snapshot or a live runtime."""


@dataclass
class NativeReadSession:
    """Derived products and counters owned by one synchronous search scope.

    ContextVar installation is restored even on an interrupted search. Reader
    handles still belong to each individual world; root seeds contain portable
    facts only. The opt-in flags enable reviewed experiments, never global state.
    """
    validate: bool = False
    field_cache: bool = True
    incremental_digests: bool = True
    generic_digests: bool = True
    seed_reuse: bool = True
    field_groups: bool = False
    knowledge_leaves: bool = False
    root_seed: object = None
    counts: Counter = field(default_factory=Counter)
    rule_groups: OrderedDict = field(default_factory=OrderedDict)
    knowledge_rows: OrderedDict = field(default_factory=OrderedDict)
    actor_revisions: bool = False
    identity_sections: bool = False
    demand_products: bool = False
    pure_rating_templates: dict = field(default_factory=dict)


@contextmanager
def native_reads(search, *, validate=False, root_information=None):
    """Admit reviewed pure consumers only; restore scope on every exit."""
    from game.ai.evaluation.action_priors import (UniformActionPrior, HeuristicActionPrior)
    from game.ai.evaluation.observation_policy import ObservationOpponentPolicy
    from game.ai.evaluation.state_value import StateValueEvaluator
    if type(search.evaluator) is not StateValueEvaluator or vars(search.evaluator):
        raise ValueError('Runtime observations require the native value evaluator')
    if type(search.action_prior) not in (UniformActionPrior, HeuristicActionPrior):
        raise ValueError('Runtime observations require a native action prior')
    for policy in (search.opponent_policy, search.rollout_policy,
                   getattr(search.action_prior, 'policy', None)):
        if policy is not None and (type(policy) is not ObservationOpponentPolicy
                or type(policy.evaluator) is not StateValueEvaluator or vars(policy.evaluator)
                or any(callable(v) for v in vars(policy).values())):
            raise ValueError('Runtime observations require native observation policies')
    session = NativeReadSession(validate, getattr(search, '_runtime_field_cache', True),
        getattr(search, '_incremental_digests', True), getattr(search, '_generic_digests', True),
        getattr(search, '_digest_seed_reuse', True), getattr(search, '_incremental_field_groups', False),
        getattr(search, '_incremental_knowledge_rows', False))
    session.actor_revisions = getattr(search, '_actor_write_revisions', False)
    session.identity_sections = getattr(search, '_actor_identity_sections', False) or getattr(search, '_actor_demand_products', False)
    session.demand_products = getattr(search, '_actor_demand_products', False)
    if session.incremental_digests and session.seed_reuse:
        session.root_seed = RootDigestSeed.information(root_information)
        if session.root_seed is not None:
            session.counts['digest_root_inputs_reused'] += 1
    token = _session.set(session)
    try:
        yield session
    finally:
        _session.reset(token)


def enabled(simulator):
    if _session.get() is None:
        return False
    if not (simulator.view_mode == 'search' and simulator.track_information and simulator.structured_records):
        raise ValueError('Runtime observations require structured, tracked search views')
    return True


@contextmanager
def snapshot_reads():
    """An explicitly ordinary nested search must not inherit native read mode."""
    token = _session.set(None)
    try:
        yield
    finally:
        _session.reset(token)


def digest_seed_reuse():
    session = _session.get()
    return session is not None and session.incremental_digests and session.seed_reuse


def prepare_digest_seeds(state):
    """Install only derived data on a trusted sampled template, without reading it."""
    if not digest_seed_reuse():
        return
    readers = getattr(state, '_observation_readers', None)
    if readers is None:
        state._observation_readers = readers = ObservationReaders()
    elif readers.seeds.root is None:
        readers.seeds.root = _session.get().root_seed


class ObservationReaders:
    """State-owned view handles and field caches, keyed by the viewing player.

    A copied world starts with empty handles so its readers cannot reach the
    source runtime. Trusted detached seed facts can be shared separately. These
    caches accelerate reading; the mutable engine state remains authoritative.
    """
    def __init__(self, seeds=None):
        self.views = {}
        from game.ai.information.runtime_field_cache import RuntimeFieldCache
        self.fields = RuntimeFieldCache()
        self.digest_counts = Counter()
        self.knowledge_rows = OrderedDict()
        self.card_digests = None
        session = _session.get()
        root = session.root_seed if session is not None and session.seed_reuse else None
        self.seeds = seeds if seeds is not None else DigestSeedBank(root)

    @contextmanager
    def shared_reads(self):
        """Share guarded card identities while one decision's runtime is fixed.

        The engine stays suspended through actor and observer projection.
        Nested projections invalidate the outer frame before it resumes.
        """
        previous = self.card_digests
        if previous is not None:
            previous.clear()
        self.card_digests = {}
        try:
            yield
        finally:
            self.card_digests = previous
            if previous is not None:
                previous.clear()

    def __deepcopy__(self, memo):
        result = type(self)(DigestSeedBank())
        memo[id(self)] = result
        return result

    def __reduce__(self):
        # RuntimeClone uses reduction rather than __deepcopy__. Handles must
        # never pull the source world's graph into a sanitized/copy snapshot.
        return type(self), (DigestSeedBank(),)


def player_view(state, player):
    if not any(player is participant for participant in state.players):
        raise ValueError('Viewer belongs to another world')
    cache = getattr(state, '_observation_readers', None)
    if cache is None:
        state._observation_readers = cache = ObservationReaders()
    if player.idx not in cache.views:
        cache.views[player.idx] = RuntimeObservation(state, player)
    return cache.views[player.idx]


class CardReadContract(Mapping, DigestReader):
    """One reusable (world, viewer, card) accessor, with no copied card row.

    Field memo lasts through one synchronous read lease. Persistent identity
    inputs are validated on reuse; native stat reuse belongs to engine caches.
    """
    shell = ('id', 'zone_revision', 'owner', 'controller', 'zone', 'face_down',
             'tapped', 'counters', 'damage_marked', 'damage_by_deathtouch',
             'attached_to', 'attachments', 'name', 'identity_visible')
    identity = ('token', 'legendary', 'types', 'subtypes', 'colors', 'keywords',
                'power', 'toughness', 'text', 'cost', 'casting_cost', 'mana_value',
                'chosen_x', 'summoning_sick', 'skip_untap', 'loyalty_activated_this_turn',
                'abilities', 'triggers', 'static_abilities', 'attachment_restrictions')

    def __init__(self, view, card):
        self.view, self.card = view, card
        self.memo = {}
        self._digest_inputs = None
        self._digest_value = None
        self._digest_base = self._digest_base_parts = None
        self._generic_digests = None
        self._rule_group = None
        self._actor_receipt = None

    def identity_digest(self):
        view = self.view
        shared = view.state._observation_readers.card_digests
        session = _session.get()
        share = (shared is not None and view._native_digest_readers
                 and view._rule_readers is _NATIVE_RULE_RENDERER_TOKEN
                 and 'read' not in vars(self)
                 and (session is None or not session.field_groups))
        if not share:
            return self._identity_digest()
        builder = view.builder
        cached = shared.get(id(self.card))
        if cached is not None and cached.matches(self.card, builder):
            view.state._observation_readers.digest_counts['digest_shared_view_hits'] += 1
            return cached.value
        # Pre-read fields may have permissions outside this capture. Such a
        # row stays on the ordinary path instead of publishing an incomplete proof.
        if self.memo:
            return self._identity_digest()
        previous, builder._row_reads = builder._row_reads, []
        try:
            value = self._identity_digest()
            # The lease interns repeated gates. Keep one copy of each proof
            # for the other viewer while nested description slices retain all
            # their original read events in the builder's list.
            reads = tuple({id(read): read for read in builder._row_reads}.values())
        finally:
            captured = builder._row_reads
            builder._row_reads = previous
            if previous is not None:
                previous.extend(captured)
        if self._digest_inputs is not None:
            shared[id(self.card)] = ProjectedCardRow(self.card, value, reads)
            view.state._observation_readers.digest_counts['digest_shared_view_publications'] += 1
        return value

    def _identity_digest(self):
        if self.view._actor_revisions:
            from game.ai.experiments.actor_identity_cache import card_digest
            return card_digest(self)
        return self._identity_digest_live()

    def _identity_digest_live(self, *, native_inputs=_UNREAD_NATIVE_INPUTS):
        # Only bytes escape the synchronous reader. The full implementation
        # remains independent, including for tests/validation and custom cards.
        self.view.builder
        inputs = native_card_inputs(self) if native_inputs is _UNREAD_NATIVE_INPUTS else native_inputs
        counts = self.view.state._observation_readers.digest_counts
        old = self._digest_inputs
        if (inputs is not None and old is not None and inputs[0] is old[0]
                and inputs[1] is old[1] and inputs[2] == old[2] and inputs[3] == old[3]):
            counts['digest_card_hits'] += 1
            return self._digest_value
        session = _session.get()
        reuse = session is None or session.seed_reuse
        readers = self.view.state._observation_readers
        player, identifier = self.view.player.idx, self.card.command_id
        if inputs is None:
            counts['digest_card_live_reads'] += 1
            if session is None or session.generic_digests:
                if self._generic_digests is None:
                    seed = readers.seeds.generic(player, identifier) if reuse else None
                    self._generic_digests = DigestNode.seeded(seed, counts)
                    if seed is not None: counts['digest_generic_seed_loads'] += 1
                before = self._generic_digests.digest
                value = self._generic_digests.mapping(self)
                if before == value:
                    counts['digest_card_hits'] += 1
                    counts['digest_generic_hits'] += 1
                else:
                    counts['digest_card_renders'] += 1
                    counts['digest_generic_renders'] += 1
                    if reuse:
                        readers.seeds.publish(player, identifier, 'generic', self._generic_digests.freeze())
                self._digest_inputs, self._digest_value = None, value
                return value
            value = structural_digest(self)
            counts['digest_full_card_hashes'] += 1
        else:
            parts = card_base_parts(self, inputs)
            seed, source = readers.seeds.native(player, identifier, inputs, parts) if reuse else (None, None)
            if seed is not None:
                self._digest_inputs, self._digest_value = inputs, seed.digest
                counts['digest_card_hits'] += 1
                counts['digest_seed_hits'] += 1
                counts['digest_seed_hits_' + source] += 1
                return seed.digest
            if self._digest_value is None:
                key = ('digest_native_first_changed' if reuse and readers.seeds.has_native(player, identifier)
                       else 'digest_native_cold')
            else:
                key = 'digest_native_changed'
            counts[key] += 1
            value = native_card_digest(self, inputs)
            if reuse:
                readers.seeds.publish(player, identifier, 'native', NativeSeed(inputs[2], parts, inputs[3], value))
        self._digest_inputs, self._digest_value = inputs, value
        counts['digest_card_renders'] += 1
        return value

    def base_characteristics(self):
        if '_base' not in self.memo:
            builder = self.view.builder
            self.memo['_base'] = native_characteristics(self.card, builder.state,
                lambda: builder._base_characteristics(self.card))
        return self.memo['_base']

    def __iter__(self):
        builder = self.view.builder
        return iter(self.shell + (self.identity if builder.knows_identity(self.card) else ()))

    def __len__(self):
        return sum(1 for _ in self)

    def __getitem__(self, key):
        builder = self.view.builder
        if key not in self.memo:
            if key not in self.shell and (key not in self.identity or not builder.knows_identity(self.card)):
                raise KeyError(key)
            self.memo[key] = self.read(key, builder)
        return self.memo[key]

    def read(self, key, builder):
        c, s = self.card, builder.state
        r = c.state
        if key == 'id': return c.command_id
        if key == 'zone_revision': return c.zone_revision
        if key == 'owner': return c.owner.idx
        if key == 'controller': return c.get_controller(s).idx
        if key == 'zone': return c.get_zone().name
        if key == 'face_down': return r.face_down
        if key == 'tapped': return c.is_tapped
        if key == 'counters': return builder.describe(r.counters)
        if key == 'damage_marked': return r.damage_marked
        if key == 'damage_by_deathtouch': return r.damage_by_deathtouch
        if key == 'attached_to': return builder.reference(c.attached_to)
        if key == 'attachments': return tuple(builder.reference(v) for v in r.attached.values() if builder.is_visible(v))
        if key == 'identity_visible': return builder.knows_identity(c)
        if key == 'name': return c.name if builder.knows_identity(c) else None
        if key == 'token': return c.is_token
        if key == 'legendary': return c.definition.legendary
        if key in ('types', 'subtypes', 'colors', 'keywords', 'power', 'toughness'):
            # This cache is engine-owned, including native modifier guards.
            base = self.base_characteristics()
            return builder.describe(base[key]) if key == 'colors' else base[key]
        if key == 'text': return c.definition.oracle_text
        if key in ('cost', 'casting_cost', 'mana_value'):
            if '_mana' not in self.memo:
                session = _session.get()
                self.memo['_mana'] = (builder._mana_values(c) if session is not None and not session.field_cache
                                      else c.get_mana_fields(s))
            return self.memo['_mana'][('cost', 'casting_cost', 'mana_value').index(key)]
        if key == 'chosen_x': return r.mana_x
        if key == 'summoning_sick': return c.get_zone() == ZoneType.BATTLEFIELD and c.is_summoning_sick(s)
        if key == 'skip_untap': return builder.describe(r.skip_untap)
        if key == 'loyalty_activated_this_turn': return c.loyalty_activated_turn == s.turn.number
        if key == 'abilities': return tuple(self.view.ability(a) for a in c.get_ability_defs(s).values())
        if key == 'triggers': return tuple(self.view.ability(a) for a in c.get_trigger_defs(s).values())
        if key == 'static_abilities': return self.view.describe(c.get_stat(STAT_STATIC_ABILITIES, s))
        if key == 'attachment_restrictions': return tuple(restriction_keywords(c.modifiers_to_attach(s)))
        raise KeyError(key)


_NATIVE_CARD_READ = CardReadContract.read


class RuntimeObservation(Mapping, DigestReader):
    """Reusable visibility window, whose data are read from its owning State."""
    fields = ('schema_version', 'turn', 'phase', 'self', 'active', 'priority',
            'passed_players', 'players', 'hand', 'knowledge', 'battlefield',
            'graveyard', 'exile', 'stack', 'pending_abilities', 'continuous_effects',
            'replacement_effects', 'combat', 'information_limits')
    limits = (
        'Opponent hands and all library identities/order are hidden.',
        'Face-down identities are masked; inspection permissions are not yet modeled.',
        'Knowledge covers published zone changes/draws; general reveal and scry permissions are not yet modeled.',
        'description_unavailable marks rule code without a declarative description; do not infer its meaning.',
    )

    def __init__(self, state, player):
        self.state, self.player = state, player
        self._builder = None
        self.rows = {}
        self.memo = {}
        self.extra = {}
        self._versions = {}
        readers = state._observation_readers
        session = _session.get()
        seed = readers.seeds.view(player.idx) if session is None or session.seed_reuse else None
        self._digests = DigestNode.seeded(seed, readers.digest_counts, root_view=True)
        if seed is not None:
            readers.digest_counts['digest_view_seed_loads'] += 1
            if session is not None:
                session.counts['digest_view_seed_loads'] += 1
        self._native_digest_readers = False
        self._rule_readers = ()
        self._actor_revisions = False
        self._lease_token = self._identity_lease = None
        self._identity_parts = None
        self._prepared_score_key = self._prepared_score = None

    def identity_digest(self):
        self.builder
        value = self._digests.mapping(self)
        session = _session.get()
        if session is not None and session.identity_sections:
            from game.ai.experiments.actor_identity_parts import capture
            self._identity_parts = capture(self._digests, self._identity_parts)
            self._identity_lease = self._lease_token
            session.counts['actor_identity_sections'] += 1
        if session is None or session.seed_reuse:
            self.state._observation_readers.seeds.publish_view(self.player.idx, self._digests)
        return value

    def identity_sections(self):
        """Exact field identities from this lease, never a previous live state."""
        self.builder
        return (self._identity_parts if self._lease_token is not None
                and self._identity_lease is self._lease_token else None)

    def ability(self, definition):
        if (session := _session.get()) is not None and not session.field_cache:
            return self.builder.ability(definition)
        return self.state._observation_readers.fields.ability(self.builder, definition, self._versions)

    def describe(self, value):
        if (session := _session.get()) is not None and not session.field_cache:
            return self.builder.describe(value)
        return self.state._observation_readers.fields.describe(self.builder, value, self._versions)

    @property
    def builder(self):
        if self._builder is None:
            raise SnapshotUnavailable('Runtime observation is outside its synchronous read lease')
        return self._builder

    @contextmanager
    def read(self, *, request=None, update_knowledge=True, action_builder=None):
        if self._builder is not None:
            raise RuntimeError('Nested read lease on the same viewer')
        if action_builder is not None:
            if (action_builder.request is not request or request.state is not self.state
                    or request.player is not self.player):
                raise ValueError('Action builder belongs to another decision')
            builder = action_builder.view
        else:
            builder = PlayerObservationBuilder(self.state, self.player)
        builder._ability_descriptions, builder._descriptions = {}, {}
        builder._visibility_proofs = ({} if self.state._observation_readers.card_digests is not None else None)
        self._builder = builder
        counts = self.state._observation_readers.fields.counts
        before_hits, before_renders = counts['hits'], counts['renders']
        digest_counts = self.state._observation_readers.digest_counts
        digest_before = digest_counts.copy()
        session = _session.get()
        self._actor_revisions = bool(session is not None and session.actor_revisions and session.incremental_digests)
        self._lease_token = object() if session is not None and session.identity_sections else None
        self._identity_lease = None
        self._native_digest_readers = (CardReadContract.read is _NATIVE_CARD_READ
            and native_readers() and (session is None or session.field_cache))
        if session is None or session.field_groups or self.state._observation_readers.card_digests is not None:
            from game.ai.experiments.runtime_card_fields import rule_readers
            self._rule_readers = rule_readers()
        else:
            self._rule_readers = ()
        try:
            if update_knowledge:
                builder.observe_knowledge()
            if request is not None:
                from game.ai.decisions.search_actions import PlayerActionBuilder
                from game.game_actions.generation.decision_abstraction.auxiliary import ScryRequest
                from dataclasses import fields
                if isinstance(request, ScryRequest):
                    builder.visible.update(request.candidates)
                # The native adapter owns the final context. Construct generic
                # descriptions only when this request has no explicit adapter.
                if action_builder is None:
                    action_builder = PlayerActionBuilder(request)
                    action_builder.__dict__['view'] = builder
                override = action_builder.context.request_context()
                if override is not None:
                    context = {'kind': type(request).__name__, **override}
                else:
                    context = {'kind': type(request).__name__, **{
                        f.name: builder.describe(getattr(request, f.name)) for f in fields(request)
                        if f.name not in {'state', 'player', 'choices'} and not f.name.startswith('_')}}
                    if isinstance(request, ScryRequest):
                        context['revealed_cards'] = tuple(self.card(c) for c in request.candidates)
                self.extra['decision_context'] = context
            evidence = getattr(self.player.controller, 'initial_search_evidence', None)
            if evidence is not None:
                self.extra['initial_search_evidence'] = evidence
            yield self
        finally:
            self._builder = None
            self._actor_revisions = False
            self._identity_lease = self._lease_token = None
            self.memo.clear()
            self.extra.clear()
            self._versions.clear()
            if (session := _session.get()) is not None:
                session.counts['field_cache_hits'] += counts['hits'] - before_hits
                session.counts['field_cache_renders'] += counts['renders'] - before_renders
                for key, value in digest_counts.items():
                    session.counts[key] += value - digest_before[key]
            # Field memo is per lease; persistent digests validate live inputs.
            for row in self.rows.values():
                row.memo.clear()
            # Tokens/removed objects must not be retained indefinitely by a view.
            present = set(self.state.get_cards())
            self.rows = {c: row for c, row in self.rows.items() if c in present}

    def card(self, card):
        if card not in self.builder.visible:
            raise ValueError('Hidden card is outside the read contract')
        if card not in self.rows:
            self.rows[card] = CardReadContract(self, card)
            if (session := _session.get()) is not None:
                session.counts['card_handles_created'] += 1
        return self.rows[card]

    def __iter__(self):
        self.builder
        return iter((*self.fields, *self.extra))

    def __len__(self):
        return len(self.fields) + len(self.extra)

    def __getitem__(self, key):
        self.builder
        if key in self.extra:
            return self.extra[key]
        if key not in self.memo:
            self.memo[key] = self.read_field(key)
        return self.memo[key]

    def read_field(self, key):
        s, p, b = self.state, self.player, self.builder
        if key == 'schema_version': return b.schema_version
        if key == 'turn': return s.turn.number
        if key == 'phase': return s.turn.phase.name
        if key == 'self': return p.idx
        if key == 'active': return s.active_player.idx
        if key == 'priority': return s.priority.current_player.idx if s.priority.current_player is not None else None
        if key == 'passed_players': return tuple(sorted(q.idx for q in s.priority.passed_players))
        if key == 'players':
            return tuple({'id': q.idx, 'name': q.name, 'life': q.health,
                'poison_counters': q.poison_counters, 'has_lost': q.has_lost,
                'hand_size': len(q.hand), 'library_size': len(q.deck),
                'maximum_hand_size': q.maximum_hand_size, 'lands_played_this_turn': q.lands_played_this_turn,
                'land_plays_per_turn': q.land_plays_per_turn,
                'mana': {m.name: n for m, n in q.mana_pool.items()}} for q in s.players)
        if key == 'hand': return tuple(self.card(c) for c in p.hand.values())
        if key in ('battlefield', 'graveyard', 'exile', 'stack'):
            return tuple(self.card(c) for c in b.public if c.get_zone().name.lower() == key)
        if key == 'knowledge':
            knowledge = getattr(p.controller, 'knowledge', None)
            session = _session.get()
            if knowledge is not None and (session is None or session.knowledge_leaves):
                from game.ai.experiments.runtime_knowledge import knowledge_view
                return knowledge_view(self, knowledge)
            return knowledge.view() if knowledge is not None else None
        if key == 'pending_abilities': return b.stack_items()
        if key == 'continuous_effects': return b.ongoing_effects()
        if key == 'replacement_effects': return b.replacements()
        if key == 'information_limits': return self.limits
        if key == 'combat':
            c = s.combat
            return {'active': c.active, 'attackers': tuple(v.command_id for v in c.attackers),
                'defenders': {v.command_id: b.reference(d) for v, d in c.attackers.items()},
                'blockers': {v.command_id: a.command_id for v, a in c.blockers.items()},
                'blocked': tuple(sorted(v.command_id for v in c.blocked)),
                'attackers_declared': c._attackers_declared, 'first_strike_step': c._first_strike_step,
                'first_damage_done': c._first_damage_done, 'regular_damage_done': c._regular_damage_done}
        raise KeyError(key)


@dataclass(frozen=True, eq=False)
class IdentityRecallEntry:
    """Full observation identity with no reference to the historical world."""
    identity: DigestReference
    kind: str = 'observation'

    def __eq__(self, other):
        from game.ai.information.search_recall import RecallEntry
        if isinstance(other, IdentityRecallEntry):
            return self.kind == other.kind and self.identity == other.identity
        return (isinstance(other, RecallEntry) and self.kind == other.kind
                and self.identity.digest == other.structured.value.digest)

    def __hash__(self):
        return hash((self.kind, self.identity))

    def describe(self):
        raise SnapshotUnavailable('Native recall retains observation identity, not historical JSON')

    readonly = describe


@dataclass(frozen=True)
class PreparedView(DigestReference):
    """Detached evaluator output and horizon metadata, plus full visible identity."""
    metadata: StructuredJSON = field(compare=False)
    score: float = field(compare=False)


@dataclass(frozen=True, eq=False)
class PreparedObservation:
    player: int
    request_kind: str
    view: PreparedView
    ratings: tuple

    @property
    def identity(self):
        return self.view

    def readonly(self):
        return self.view.metadata.value.readonly

    def observation(self):
        raise SnapshotUnavailable('Detached native decision has no historical observation export')

    @property
    def key(self):
        raise SnapshotUnavailable('Legacy JSON-based keys require snapshots; use PlayerInformation.fast_key')

    def __eq__(self, other):
        from game.ai.information.search_observation import ActingPlayerObservation
        return (isinstance(other, (ActingPlayerObservation, PreparedObservation))
                and self.player == other.player and self.request_kind == other.request_kind
                and self.view.digest == structural_digest(other.identity))

    def __hash__(self):
        return hash((self.player, self.request_kind, DigestReference(self.view.digest)))


def prepared_view(view, *, store=None, factory=PreparedView):
    from game.ai.evaluation.state_value import StateValueEvaluator
    session = _session.get()
    digest = (view.identity_digest() if session is None or session.incremental_digests
              else structural_digest(view))
    if session is not None and session.validate:
        assert digest == structural_digest(view), 'Incremental observation digest differs from full hashing'
    metadata = {'self': view['self'], 'players': tuple({'id': p['id']} for p in view['players']),
                'pending_abilities': bool(view['pending_abilities']),
                'combat': view['combat']}
    if 'decision_context' in view:
        metadata['decision_context'] = view['decision_context']
    evaluator = StateValueEvaluator()
    if session is not None and session.demand_products:
        from game.ai.evaluation.prepared_products import prepared_score
        score = prepared_score(view, session, evaluator)
    elif session is not None and session.incremental_digests:
        # The identity traversal has already read and hashed these exact inputs.
        # Reuse its three child digests without materializing IdentityParts or
        # re-reading card fields solely to establish a value-cache key.
        from game.ai.evaluation.prepared_products import prepared_score
        names, parts = view._digests.shape[1], view._digests.parts
        material_key = tuple(parts[names.index(key)] for key in ('self', 'players', 'battlefield'))
        score = prepared_score(view, session, evaluator, material_key=material_key)
    else:
        score = evaluator.evaluate(view)
    result = factory(digest, StructuredJSON(metadata, store), score)
    if (session := _session.get()) is not None:
        session.counts['prepared_views'] += 1
    return result


def project_actor(replay, request, options, model, *, prepare_view=None):
    """Build exact actor products once; optional producers share this contract."""
    from game.ai.decisions.search_actions import PlayerActionBuilder
    from game.ai.decisions.search_decision import (DecisionProjector, ProjectedDecision)
    from game.ai.information.search_recall import (PlayerRecall, PlayerInformation)
    from game.ai.evaluation.observation_policy import _Scorer
    from game.ai.evaluation.state_value import StateValueEvaluator
    simulator = replay.simulator
    actor, prior = request.player.idx, replay.recalls[request.player.idx]
    projector = DecisionProjector(request, options, prior, store=simulator.value_store,
        candidate_model=model.identity if model else None, menu_complete=replay.candidate_complete,
        candidate_width=replay.frontier.width if replay.frontier else 0)
    action_builder = PlayerActionBuilder(request, store=simulator.value_store)
    with player_view(request.state, request.player).read(request=request, action_builder=action_builder) as view:
        bindings = projector._bind_options(action_builder)
        detached = (prepare_view or prepared_view)(view, store=simulator.value_store)
        identity = DigestReference(structural_digest({'contract_version': 2,
            'request_kind': type(request).__name__, 'view': detached}))
        entry = IdentityRecallEntry(identity)
        knowledge = prior.knowledge.observe(view)
        recall = prior._append_entry(entry, RecallDigest(prior.digest, 'observation', identity), knowledge)
        session = _session.get()
        # These native branches read only their action kind. Their detached
        # templates need no card map, memory lookup or live scorer construction.
        from game.ai.evaluation.prepared_products import pure_ratings
        actions = tuple(action for action, _ in bindings)
        ratings = pure_ratings(actions, session)
        if ratings is None:
            remembered = knowledge.counter_sources(view)
            scorer = _Scorer(view, StateValueEvaluator(), remembered)
            ratings = tuple(sorted((scorer.rate(action) for action, _ in bindings),
                key=lambda r: (-r.score, r.action.kind != 'PassPriorityAction', r.action.sort_key)))
        current = PreparedObservation(actor, type(request).__name__, detached, ratings)
        info = PlayerInformation(current, recall, actions,
            projector.candidate_model, projector.menu_complete, projector.candidate_width)
        session = _session.get()
        session.counts['actor_decisions'] += 1
        if session.validate:
            reference = projector.build().information
            assert reference == info, 'Runtime actor identity/recall/menu differs'
            assert reference.fast_key == info.fast_key, 'Runtime information key differs'
            from game.ai.evaluation.observation_policy import ObservationOpponentPolicy
            assert ObservationOpponentPolicy().rank(reference) == ratings, 'Runtime ratings differ'
            assert StateValueEvaluator().evaluate(reference.current.readonly()) == detached.score
            session.counts['validated_actor_decisions'] += 1
    return ProjectedDecision(info, bindings), ((actor, detached),)


def project_decision(replay, request, options, model):
    """Consume actor's live window, then retain only immutable decision products."""
    from game.ai.evaluation.state_value import StateValueEvaluator
    simulator = replay.simulator
    actor = request.player.idx
    observer = next((p for p in request.participants if p.idx != actor and p.idx == simulator.observer), None)
    scope = (player_view(request.state, request.player).state._observation_readers.shared_reads()
             if observer is not None else nullcontext())
    with scope:
        projected, actor_views = project_actor(replay, request, options, model)
        views = list(actor_views)
        session = _session.get()
        if observer is not None:
            with player_view(request.state, observer).read(update_knowledge=False) as view:
                result = prepared_view(view, store=simulator.value_store)
                if session.validate:
                    from game.ai.information.observation import observation
                    full = observation(request.state, observer, update_knowledge=False)
                    evidence = getattr(observer.controller, 'initial_search_evidence', None)
                    if evidence is not None:
                        full['initial_search_evidence'] = evidence
                    assert structural_digest(full) == result.digest
                    assert StateValueEvaluator().evaluate(full) == result.score
                    session.counts['validated_observer_views'] += 1
                views.append((observer.idx, result))
    return projected, tuple(sorted(views))


def terminal_views(replay, state, participants):
    result = []
    for player in participants:
        if player.idx == replay.simulator.observer:
            with player_view(state, player).read(update_knowledge=False) as view:
                result.append((player.idx, prepared_view(view, store=replay.simulator.value_store)))
    return tuple(result)
