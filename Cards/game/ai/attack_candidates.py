"""Visible combat-risk estimates for lazy attack proposals, never rule pruning."""
from dataclasses import dataclass
from enum import IntEnum


class AttackRisk(IntEnum):
    """!
    @brief Order draft preferences; these labels are estimates, not solved outcomes.
    """
    UNBLOCKED = 0
    SAFE = 1
    MULTIBLOCK = 2
    TRADE = 3
    UNFAVOURABLE = 4
    UNKNOWN = 5


@dataclass(frozen=True)
class CombatantFacts:
    """!
    @brief Public characteristics read once during one immutable proposal pass.
    @var power Nonnegative combat power.
    @var toughness Current toughness before marked damage.
    @var damage Already marked damage.
    @var keywords Current public keywords.
    """
    power: int
    toughness: int
    damage: int
    keywords: frozenset

    def deals(self, early):
        """!
        @brief Indicate participation in the first or regular damage step.
        """
        first = bool(self.keywords & {'first strike', 'double strike'})
        return first if early else not first or 'double strike' in self.keywords


class AttackRiskAssessment:
    """!
    @brief Estimate losses from public blockers in O(attackers * blockers) pair checks.

    No hands, libraries or hidden card definitions participate. Spells, activated
    abilities, replacement effects and triggered combat effects are not simulated.
    Even SAFE means only survival in this static damage estimate.
    """
    def __init__(self, state):
        self.state = state
        self.facts = {}
        self.blocker_sets = {}
        self.risks = {}
        self.pair_checks = 0

    def read(self, card):
        """!
        @brief Cache public facts, leaving face-down characteristics uninspected.
        """
        if card not in self.facts:
            if card.state.face_down:
                self.facts[card] = None
            else:
                from game.stat_type import STAT_KEYWORDS
                self.facts[card] = CombatantFacts(
                    max(0, card.get_power(self.state) or 0),
                    card.get_toughness(self.state) or 0,
                    card.state.damage_marked,
                    frozenset(card.get_stat(STAT_KEYWORDS, self.state)))
        return self.facts[card]

    @staticmethod
    def duel(attacker, blocker):
        """!
        @brief Estimate both deaths across damage steps for a single legal block.
        """
        a_damage, b_damage = attacker.damage, blocker.damage
        a_dead = b_dead = False
        for early in (True, False):
            if a_dead or b_dead:
                break
            a_hit = attacker.power if attacker.deals(early) else 0
            b_hit = blocker.power if blocker.deals(early) else 0
            a_damage += b_hit
            b_damage += a_hit
            a_dead = ('indestructible' not in attacker.keywords and
                      (a_damage >= attacker.toughness or b_hit > 0 and 'deathtouch' in blocker.keywords))
            b_dead = ('indestructible' not in blocker.keywords and
                      (b_damage >= blocker.toughness or a_hit > 0 and 'deathtouch' in attacker.keywords))
        return a_dead, b_dead

    def classify(self, attacker, opponent, blockers):
        """!
        @brief Classify one attacker without constructing blocker assignments.

        Summed blocker damage is an upper bound: multi-block ordering is not
        searched. Menace requires a group; unsupported damage mechanics stay
        UNKNOWN instead of being advertised as a safe trade.
        """
        a = self.read(attacker)
        if a is None:
            return AttackRisk.UNKNOWN
        blockers = tuple(blockers)
        from game.rules.combat import CombatState
        from game.game_state import Card
        # The reviewed native hypothetical blocker check observes attacker
        # keywords only. Keep every concrete blocker in this local key. Custom
        # combat implementations retain all pair checks. This shares heuristic
        # computation, never declarations, information keys or outcome visits.
        key = ((opponent, blockers, a.keywords)
               if type(self.state.combat) is CombatState and type(attacker) is Card else None)
        if key is not None and (a, key) in self.risks:
            return self.risks[a, key]
        if key is not None and key in self.blocker_sets:
            legal = self.blocker_sets[key]
        else:
            legal = self.legal_facts(attacker, opponent, blockers)
            if key is not None:
                self.blocker_sets[key] = legal
        risk = self.classify_facts(a, legal)
        if key is not None:
            self.risks[a, key] = risk
        return risk

    def legal_facts(self, attacker, opponent, blockers):
        """!
        @brief Check native hypothetical pairs for one public evasion profile.
        """
        legal = []
        for blocker in blockers:
            b = self.read(blocker)
            if b is None:
                return None
            self.pair_checks += 1
            if self.state.combat.blocker_error(blocker, attacker, opponent, check_assignment=False) is None:
                legal.append(b)
        return tuple(legal)

    def classify_facts(self, a, legal):
        """!
        @brief Rate the static matchup after identity-dependent legality checks.
        """
        if legal is None:
            return AttackRisk.UNKNOWN
        minimum = 2 if 'menace' in a.keywords else 1
        if len(legal) < minimum:
            return AttackRisk.UNBLOCKED
        uncertain = {'infect', 'wither', 'must be blocked by all'}
        if a.keywords & uncertain or any(b.keywords & uncertain for b in legal):
            return AttackRisk.UNKNOWN
        if minimum == 1:
            trade = False
            for b in legal:
                dies, kills = self.duel(a, b)
                if dies and not kills:
                    return AttackRisk.UNFAVOURABLE
                trade |= dies
            if trade:
                return AttackRisk.TRADE
        damage = sum(b.power * (2 if 'double strike' in b.keywords else 1) for b in legal)
        lethal_touch = any(b.power > 0 and 'deathtouch' in b.keywords for b in legal)
        if (len(legal) > 1 and 'indestructible' not in a.keywords and
                (damage >= a.toughness - a.damage or lethal_touch)):
            return AttackRisk.MULTIBLOCK
        return AttackRisk.SAFE


class RiskAttackersStrategy:
    """!
    @brief Yield a small family of attack drafts before any subset enumeration.

    Complete all-in is first, including zero-power attackers with attack effects.
    Risk groups are only proposal ordering; the caller supplies deduplication,
    native legality checks and an eventual exhaustive continuation.
    """
    def __init__(self, width):
        self.width = width

    def generate(self, request):
        """!
        @brief Generate all-in, no attack, risk-group and bounded pressure drafts.
        """
        from game.game_actions.generation.decision_abstraction.options import DeclareAttackersOption
        state, player = request.state, request.player
        assessment = AttackRiskAssessment(state)
        def order(card):
            facts = assessment.read(card)
            return -(facts.power if facts else 0), str(card.command_id)
        attackers = tuple(sorted(state.combat.legal_attackers(player), key=order))
        for opponent in state.active_players:
            if opponent is player:
                continue
            yield DeclareAttackersOption(dict.fromkeys(attackers, opponent))
            blockers = state.combat.potential_blockers(opponent)
            risks = {card: assessment.classify(card, opponent, blockers) for card in attackers}
            safe = tuple(c for c in attackers if risks[c] <= AttackRisk.SAFE)
            yield DeclareAttackersOption(dict.fromkeys(safe, opponent))
            yield DeclareAttackersOption({})
            for limit in (AttackRisk.MULTIBLOCK, AttackRisk.TRADE):
                yield DeclareAttackersOption({c: opponent for c in attackers if risks[c] <= limit})
            # Within a group preserve deterministic power/ID order. Only a
            # bounded number of prefixes is materialized, never a powerset.
            ordered = tuple(c for risk in AttackRisk for c in attackers if risks[c] == risk)
            for count in range(1, min(len(ordered), self.width) + 1):
                yield DeclareAttackersOption(dict.fromkeys(ordered[:count], opponent))


class PowerAttackersStrategy:
    """!
    @brief Preserve the original power-prefix baseline for controlled comparisons.
    """
    def __init__(self, width):
        self.width = width

    def generate(self, request):
        """!
        @brief Yield the previous all-positive, single-block-safe and power drafts.
        """
        from game.ai.combat_strategies import CombatEvaluator
        from game.game_actions.generation.decision_abstraction.options import DeclareAttackersOption
        state, player = request.state, request.player
        attackers = sorted(state.combat.legal_attackers(player),
                           key=lambda c: (-(c.get_power(state) or 0), c.command_id))
        evaluator = CombatEvaluator()
        for opponent in state.active_players:
            if opponent is player:
                continue
            all_attack = {c: opponent for c in attackers if (c.get_power(state) or 0) > 0}
            yield DeclareAttackersOption(all_attack)
            defenders = evaluator.defenders(state, opponent)
            yield DeclareAttackersOption({c: opponent for c in all_attack
                if evaluator.safe_attacker(state, c, defenders)})
            yield DeclareAttackersOption({})
            for count in range(1, min(len(attackers), self.width) + 1):
                yield DeclareAttackersOption(dict.fromkeys(attackers[:count], opponent))
