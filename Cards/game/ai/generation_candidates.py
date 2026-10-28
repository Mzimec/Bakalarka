"""Deterministic anytime proposals, followed by exhaustive native enumeration.

Proposals never own legality. The selection pipelines validate each complete
declaration. The beam is fixed, independent of the requested prefix size, so
growing a frontier cannot reorder or remove previously admitted candidates.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace

_ranking = ContextVar('search_target_ranking', default=False)
_native_ranking = ContextVar('native_search_proposals', default=None)


def target_order(candidates, source, controller, state, resolver=None):
    """!
    @brief Rank visible targets before constructing expensive target combinations.
    """
    native = _native_ranking.get()
    if native is not None:
        from game.target.target_resolver import TargetResolver
        from game.target.target_selector import SingleTargetSelector, MinMaxTegetSelector
        # Authored generators may deliberately retain just their first group.
        # Reordering their inputs would change the authored legal subset.
        if type(resolver) is not TargetResolver or type(resolver.target_selector) not in (SingleTargetSelector, MinMaxTegetSelector):
            return candidates
        return native.order_targets(candidates, source, controller, state, resolver)
    if not _ranking.get():
        return candidates
    values = tuple(candidates)
    def score(card):
        from game.enums import ZoneType
        from game.game_state import Card
        if not isinstance(card, Card):
            return 0, str(getattr(card, 'idx', ''))
        # Read characteristics only where this actor can know them. Hidden
        # opponent identities/order never participate in candidate scores.
        zone = card.get_zone() if hasattr(card, 'get_zone') else None
        visible = zone in (ZoneType.BATTLEFIELD, ZoneType.GRAVEYARD, ZoneType.EXILE, ZoneType.STACK)
        own_hand = zone == ZoneType.HAND and card.owner is controller
        value = 0
        if (visible or own_hand) and not card.state.face_down:
            value = max(0, card.get_power(state) or 0) + max(0, card.get_toughness(state) or 0)
        return -value, str(getattr(card, 'command_id', getattr(card, 'idx', '')))
    return tuple(sorted(values, key=score))


@contextmanager
def candidate_ranking(*, native=False):
    """!
    @brief Scope deterministic ranking to the current generation operation.
    """
    from game.ai.native_candidates import NativeProposalContext
    token = _ranking.set(True)
    native_token = _native_ranking.set(NativeProposalContext() if native else None)
    try:
        yield
    finally:
        _ranking.reset(token)
        _native_ranking.reset(native_token)


def target_preferences(effects):
    """!
    @brief Scope effect-specific preferences without changing native legality.
    """
    native = _native_ranking.get()
    return native.plan(effects) if native is not None else nullcontext()


def ability_order(candidates, state, controller):
    """!
    @brief Rank native source abilities before generating their complete executions.
    """
    native = _native_ranking.get()
    return native.order_abilities(candidates, state, controller) if native is not None else candidates


@dataclass(frozen=True)
class CandidateExpansion:
    """!
    @brief Request a wider prefix from the current native candidate frontier.
    @var limit Requested admitted width of the native frontier.
    """
    limit: int


class CandidateFrontier:
    """!
    @brief Own a resumable legal candidate stream and its admitted prefix.
    """
    def __init__(self, stream, maximum):
        self.stream, self.maximum = iter(stream), maximum
        self.options, self.complete = [], False
        self._prefixes = {}

    def take(self, count, *, lookahead=True):
        """!
        @brief Advance the legal stream only far enough to admit the requested bounded prefix.
        """
        if type(count) is not int or count < 1 or count > self.maximum:
            raise ValueError('Candidate frontier width is outside its explicit work cap')
        self.width = count
        while not self.complete and len(self.options) < count + bool(lookahead):
            try:
                self.options.append(next(self.stream))
            except StopIteration:
                self.complete = True
        key = min(count, len(self.options))
        prefix = self._prefixes.get(key)
        if prefix is None:
            # One immutable tuple per admitted width; subsequent reads neither
            # restart generation nor allocate another copy of the same prefix.
            if len(self._prefixes) >= 8:
                self._prefixes.clear()
            prefix = self._prefixes[key] = tuple(self.options[:count])
        return prefix, self.complete and len(self.options) <= count

    def close(self):
        """!
        @brief Release owned streams, worker pools or optional execution resources.
        """
        close = getattr(self.stream, 'close', None)
        if close is not None:
            close()


class BeamCombatStrategy:
    """!
    @brief Propose bounded combat assignments before exhaustive native enumeration.
    """
    def __init__(self, *, blockers, width):
        self.blockers, self.width = blockers, width

    def generate(self, request):
        """!
        @brief Yield native proposals under the configured action-generation policy.
        """
        from game.game_actions.generation.decision_abstraction.selection_strategies import (
            FullAttackersStrategy, FullBlockersStrategy, combat_view,
        )
        from game.game_actions.generation.decision_abstraction.options import DeclareAttackersOption, DeclareBlockersOption
        state, player = request.state, request.player
        combat = combat_view(state)
        if self.blockers:
            objects = tuple(dict.fromkeys(b for a in combat.attackers for b in combat.legal_blockers(player, a)))
            alternatives = [(None, *(a for a in combat.attackers if combat.blocker_error(b, a, player) is None)) for b in objects]
            option_type, exhaustive = DeclareBlockersOption, FullBlockersStrategy()
        else:
            objects = tuple(combat.legal_attackers(player))
            # Full native enumeration supplies all other defenders after the
            # cheap player-focused proposals, including planeswalkers.
            defenders = tuple(p for p in state.active_players if p is not player)
            alternatives = [(None, *defenders) for _ in objects]
            option_type, exhaustive = DeclareAttackersOption, FullAttackersStrategy()
        power = {card: max(0, card.get_power(state) or 0) for card in (*objects, *combat.attackers)}
        toughness = {card: max(0, card.get_toughness(state) or 0) for card in (*objects, *combat.attackers)}
        def score(assignment):
            if not self.blockers:
                return sum(power[card] for card in assignment)
            value = 0
            for attacker in combat.attackers:
                group = [b for b, a in assignment.items() if a is attacker]
                if group:
                    value += power[attacker] * (8 if power[attacker] >= player.health else 1)
                    if sum(power[b] for b in group) >= toughness[attacker]:
                        value += power[attacker] + toughness[attacker]
                    value -= sum(power[b] + toughness[b] for b in group if power[attacker] >= toughness[b])
            return value
        beam = [dict()]
        for card, choices in zip(objects, alternatives):
            proposals = []
            for prefix in beam:
                for target in choices:
                    candidate = dict(prefix)
                    if target is not None:
                        candidate[card] = target
                    proposals.append(candidate)
            proposals.sort(key=lambda candidate: (-score(candidate), tuple(
                (str(c.command_id), str(getattr(t, 'command_id', getattr(t, 'idx', '')))) for c, t in candidate.items())))
            beam = proposals[:self.width]
        seen = set()
        greedy = []
        if self.blockers:
            from game.ai.combat_strategies import GreedyBlockersStrategy
            greedy = [option.declarations for option in GreedyBlockersStrategy().generate(request)]
        for candidate in (*greedy, {}, *beam):
            key = frozenset(candidate.items())
            if key not in seen:
                seen.add(key)
                yield option_type(candidate)
        # Eventual completeness, including unusual must-block/defender rules.
        for option in exhaustive.generate(request):
            key = frozenset(option.declarations.items())
            if key not in seen:
                seen.add(key)
                yield option


@dataclass(frozen=True)
class DeterministicCandidates:
    """!
    @brief Immutable settings for deterministic anytime native action proposals.
    @var initial_width Initial width retained by this record.
    @var beam_width Beam width retained by this record.
    @var rank_window Rank window retained by this record.
    @var rank_windows Rank windows retained by this record.
    @var native_ordering Native ordering retained by this record.
    @var attack_risk Enable experimental static combat-risk proposal groups.
    @var combat_equivalence Generate declaration-role representatives before concrete alternatives.
    """
    initial_width: int = 4
    beam_width: int = 32
    rank_window: int = 0
    rank_windows: tuple = ()
    native_ordering: bool = False
    attack_risk: bool = False
    combat_equivalence: bool = False

    def __post_init__(self):
        if any(type(n) is not int or n < 2 for n in (self.initial_width, self.beam_width)):
            raise ValueError('Candidate widths must be integers >= 2')
        from game.ai.candidate_ranking import rank_window, normalize_rank_windows
        rank_window(self.rank_window)
        object.__setattr__(self, 'rank_windows', normalize_rank_windows(self.rank_windows))
        if type(self.native_ordering) is not bool:
            raise ValueError('native_ordering must be boolean')
        if type(self.attack_risk) is not bool or self.attack_risk and not self.native_ordering:
            raise ValueError('attack_risk requires native_ordering and must be boolean')
        if type(self.combat_equivalence) is not bool or self.combat_equivalence and not self.native_ordering:
            raise ValueError('combat_equivalence requires native_ordering and must be boolean')
        if self.native_ordering and (self.rank_window or self.rank_windows):
            raise ValueError('Native ordering replaces completed-batch ranking')

    @property
    def identity(self):
        """!
        @brief Describe candidate settings used to scope compatible information keys.
        """
        base = f'native-candidates-v1:{self.initial_width}:{self.beam_width}'
        if self.native_ordering:
            native = (f'{base}:native-proposals-v2:attack-risk-v1' if self.attack_risk else
                      f'{base}:native-proposals-v1')
            return f'{native}:combat-roles-v1' if self.combat_equivalence else native
        if self.rank_window or self.rank_windows:
            return f'{base}:rank-v1:{self.rank_window}:{self.rank_windows!r}'
        return base

    def combat_reservoir_limit(self, request, width, maximum):
        """!
        @brief Bound root prefetch by the available declaration-role prefix.

        Only bounded integer representatives are inspected, never a concrete
        product. The local context is discarded before returning the limit.
        """
        if not self.combat_equivalence:
            return maximum
        from game.ai.combat_equivalence import CombatDeclarationEquivalence
        from itertools import islice
        context = CombatDeclarationEquivalence(request, blockers=type(request).__name__ == 'DeclareBlockersRequest')
        if not context.valid or not context.reduced:
            return maximum
        count = sum(1 for _ in islice(context.representatives(), maximum))
        return max(width, count)

    def generate(self, request, policy):
        """!
        @brief Yield native proposals under the configured action-generation policy.
        """
        from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest, DeclareBlockersRequest
        from game.game_actions.generation.decision_abstraction.policies import DeclareAttackersPolicy, DeclareBlockersPolicy
        from game.ai.native_candidates import NativeCombatStrategy
        combat_strategy = NativeCombatStrategy if self.native_ordering else BeamCombatStrategy
        if self.combat_equivalence:
            from game.ai.combat_equivalence import EquivalentCombatStrategy
            combat_strategy = EquivalentCombatStrategy
        strategy_options = {'attack_risk': self.attack_risk} if self.native_ordering else {}
        if type(request) is DeclareAttackersRequest:
            policy = replace(policy or DeclareAttackersPolicy(), strategy=combat_strategy(
                blockers=False, width=self.beam_width, **strategy_options))
        elif type(request) is DeclareBlockersRequest:
            policy = replace(policy or DeclareBlockersPolicy(), strategy=combat_strategy(
                blockers=True, width=self.beam_width, **strategy_options))
        from game.ai.candidate_ranking import generation_family, ranked_batches, VisibleCandidateRanker
        window = dict(self.rank_windows).get(generation_family(request), self.rank_window)
        with candidate_ranking(native=self.native_ordering):
            yield from ranked_batches(request.option_space(policy), window, VisibleCandidateRanker(request))
