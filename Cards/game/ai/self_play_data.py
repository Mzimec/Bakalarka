"""Terminal-labelled examples, visit targets and bounded learner-owned replay."""
from collections import Counter, deque
from dataclasses import dataclass, asdict
from hashlib import sha256
from math import exp, fsum, isfinite, log

from game.ai.training_features import STATE_SIZE, ACTION_SIZE, FEATURE_VERSION, encode_training
from game.ai.search_encoding import SCHEMA_VERSION

DATA_VERSION = 1


def visit_policy(visits, temperature=1.):
    """!
    @brief Normalize completed action visits into a self-play policy target.
    """
    if not visits or any(type(n) is not int or n < 0 for n in visits) or not any(visits):
        raise ValueError('Need nonnegative root visits with positive total')
    if not isfinite(temperature) or temperature < 0:
        raise ValueError('Invalid behavior temperature')
    if temperature == 0:
        best = max(visits)
        index = visits.index(best)
        return tuple(float(i == index) for i in range(len(visits)))
    maximum = max(visits)
    weights = tuple(exp((log(n)-log(maximum))/temperature) if n else 0. for n in visits)
    total = fsum(weights)
    return tuple(w/total for w in weights)


@dataclass(frozen=True)
class TrainingExample:
    """!
    @brief Detached model input, visit-policy target and final game outcome.
    @var state State retained by this record.
    @var actions Complete visible actions admitted at the current width.
    @var policy Policy retained by this record.
    @var outcome Outcome retained by this record.
    @var episode Episode retained by this record.
    @var player Index of the player owning these facts.
    @var decision Private replay address, or None at a terminal/cutoff.
    @var revision Revision retained by this record.
    @var generation Generation retained by this record.
    @var action_keys Action keys retained by this record.
    @var visits Number of completed trajectory samples.
    @var behavior Behavior retained by this record.
    @var selected Selected retained by this record.
    @var information_key Information key retained by this record.
    @var root_coverage Root coverage retained by this record.
    @var search_value Search value retained by this record.
    @var split Split retained by this record.
    @var data_version Data version retained by this record.
    @var encoder_version Encoder version retained by this record.
    @var feature_version Feature version retained by this record.
    """
    state: tuple
    actions: tuple
    policy: tuple
    outcome: float
    episode: str
    player: int
    decision: int
    revision: str
    generation: int
    action_keys: tuple
    visits: tuple
    behavior: tuple
    selected: int
    information_key: str
    root_coverage: bool
    search_value: float | None
    split: str
    data_version: int = DATA_VERSION
    encoder_version: int = SCHEMA_VERSION
    feature_version: int = FEATURE_VERSION

    def __post_init__(self):
        if (self.data_version,self.encoder_version,self.feature_version) != (DATA_VERSION,SCHEMA_VERSION,FEATURE_VERSION):
            raise ValueError('Incompatible training example version')
        n = len(self.actions)
        if len(self.state) != STATE_SIZE or n < 2 or any(len(a) != ACTION_SIZE for a in self.actions):
            raise ValueError('Invalid example feature shape')
        if any(not isfinite(x) for row in (self.state,*self.actions) for x in row):
            raise ValueError('Nonfinite training example')
        if self.outcome not in (-1.,0.,1.) or self.player not in (0,1) or self.split not in ('train','validation'):
            raise ValueError('Invalid terminal label/player/split')
        if any(len(row) != n for row in (self.policy,self.action_keys,self.visits,self.behavior)) or len(set(self.action_keys)) != n:
            raise ValueError('Example must describe exactly one complete menu')
        expected = visit_policy(self.visits)
        if any(abs(a-b)>1e-9 for a,b in zip(self.policy,expected)):
            raise ValueError('Policy target must be normalized raw visits')
        if (any(not isfinite(p) or p < 0 for p in self.behavior) or abs(fsum(self.behavior)-1)>1e-9
                or not 0 <= self.selected < n or self.behavior[self.selected] <= 0):
            raise ValueError('Invalid behavior policy/selected action')
        if self.search_value is not None and (not isfinite(self.search_value) or not -1 <= self.search_value <= 1):
            raise ValueError('Invalid search value metadata')

    def to_dict(self):
        """!
        @brief Return an explicit detached training-example representation.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """!
        @brief Validate detached example data before accepting it.
        """
        data = dict(data)
        data['actions'] = tuple(tuple(0.0 if x == 0 else x for x in row) for row in data['actions'])
        for key in ('state','policy','action_keys','visits','behavior'):
            data[key] = tuple(0.0 if key == 'state' and x == 0 else x for x in data[key])
        return cls(**data)


class EpisodeRecorder:
    """!
    @brief Record visible decisions and assign outcomes to completed episode examples.
    """
    def __init__(self, *, episode, player, generation, revision, rng,
                 temperature=1., temperature_drop_turn=8, validation_fraction=.1, max_training_actions=256):
        self.episode, self.player, self.generation, self.revision = episode,player,generation,revision
        self.rng, self.temperature, self.drop = rng,temperature,temperature_drop_turn
        self.max_training_actions = max_training_actions
        fraction = int.from_bytes(sha256(episode.encode()).digest()[:8], 'big')/2**64
        self.split = 'validation' if fraction < validation_fraction else 'train'
        self.pending, self.skipped, self.reasons = [], Counter(), Counter()
        self.decisions = 0
        self.encode = encode_training

    def choose(self, info, result):
        """!
        @brief Choose an offered action using the configured visit distribution and temperature.
        """
        self.decisions += 1
        if info.current.player != self.player:
            raise ValueError('Self-play recorder belongs to another player')
        if result.used_fallback:
            raise ValueError('Fallback results are not self-play targets')
        rows = {row.action:row for row in result.estimates}
        # Stable tie order, also the exact order passed to the action encoder.
        visits = tuple(rows[a].visits for a in info.actions)
        turn = info.current.observation().get('turn',0)
        behavior = visit_policy(visits, self.temperature if turn < self.drop else 0.)
        selected = self.rng.choices(range(len(visits)), behavior)[0]
        if not info.menu_complete or not result.metrics.root_menu_complete:
            self.skipped['incomplete_menu'] += 1
        elif result.metrics.interrupted_trajectories:
            self.skipped['censored_search'] += 1
        elif result.metrics.reused_root_visits:
            self.skipped['reused_root_visits'] += 1
        elif len(info.actions) > self.max_training_actions:
            self.skipped['training_menu_limit'] += 1
        else:
            state, actions = self.encode(info)
            self.pending.append(dict(state=state, actions=actions, policy=visit_policy(visits),
                episode=self.episode, player=self.player, decision=self.decisions,
                revision=self.revision, generation=self.generation, action_keys=tuple(a.key for a in info.actions),
                visits=visits, behavior=behavior, selected=selected, information_key=info.key,
                root_coverage=result.root_coverage_complete, search_value=rows[info.actions[selected]].mean_value, split=self.split))
        return info.actions[selected]

    def observe(self, request, detail):
        # The callback receives a live request for logging compatibility. It is
        # deliberately ignored: features only enter through detached choose().
        """!
        @brief Record a supported detached search decision as a potential training example.
        """
        self.reasons[detail['reason']] += 1

    def finish(self, result):
        """!
        @brief Attach final outcomes and accept supported completed-episode examples.
        """
        if result.status not in ('win','draw'):
            self.skipped['nonterminal_episode'] += len(self.pending)
            return ()
        if result.status == 'win' and result.winner_index not in (0,1):
            raise ValueError('Terminal win missing winner')
        outcome = 0. if result.status == 'draw' else (1. if result.winner_index == self.player else -1.)
        return tuple(TrainingExample(outcome=outcome, **data) for data in self.pending)


class ReplayBuffer:
    """!
    @brief Own a bounded in-memory sequence of accepted training examples.
    """
    def __init__(self, capacity):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('Replay capacity must be positive')
        self.capacity, self.rows, self.keys, self.total_seen = capacity,deque(),set(),0

    def add(self, row):
        """!
        @brief Admit accepted examples under the replay capacity bound.
        """
        if not isinstance(row,TrainingExample) or row.split != 'train':
            raise ValueError('Replay accepts training examples only')
        key = row.episode,row.player,row.decision
        if key in self.keys:
            raise ValueError('Duplicate replay example')
        if len(self.rows) == self.capacity:
            old = self.rows.popleft()
            self.keys.remove((old.episode,old.player,old.decision))
        self.rows.append(row)
        self.keys.add(key)
        self.total_seen += 1

    def sample(self, size, rng):
        """!
        @brief Draw a sample using the caller-owned random stream.
        """
        if type(size) is not int or not 1 <= size <= len(self.rows):
            raise ValueError('Replay batch exceeds available examples')
        rows = tuple(self.rows)
        return tuple(rows[i] for i in rng.sample(range(len(rows)),size))

