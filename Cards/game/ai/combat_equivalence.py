"""Public, decision-local combat roles and lazy representative declarations.

The certificate covers a reviewed declaration model, not future game outcomes,
recall equivalence or belief distributions. Concrete exhaustive continuation is
retained by the caller. Runtime cards below never escape the generation pass.
"""
from collections import Counter
from dataclasses import dataclass, field
from itertools import product
from math import factorial


@dataclass(frozen=True)
class CardRoleSignature:
    """!
    @brief Reviewed public facts; opaque or bound cards receive singleton roles.
    @var facts Hashable native definition, characteristic and runtime facts.
    """
    facts: tuple


@dataclass(frozen=True)
class CombatActionClass:
    """!
    @brief One declaration orbit in the local role model, with a concrete binding.
    @var key Counts or sorted blocker bundles, independent of member selection.
    @var multiplicity Number of concrete declarations represented by this class.
    @var binding Concrete native objects belonging only to this generation pass.
    """
    key: tuple
    multiplicity: int
    binding: tuple = field(repr=False, compare=False)

    def bind(self):
        """!
        @brief Return a fresh native declaration for the normal legality pipeline.
        """
        return dict(self.binding)


class CombatRoles:
    """!
    @brief Group only reviewed public participants without looking in hidden zones.

    Printed definitions are kept distinct. Attachments, anchored effects,
    additional modifier sources, hidden faces and unreviewed rules stay singleton.
    This does not replace the engine's information key or read player histories.
    """
    def __init__(self, state):
        self.state = state
        self.signatures = {}
        self.definitions = []
        self.fact_reads = 0

    def signature(self, card):
        """!
        @brief Read a conservative role once in this immutable declaration pass.
        """
        if card not in self.signatures:
            self.signatures[card] = self._signature(card)
        return self.signatures[card]

    def _signature(self, card):
        from game.game_state import Card
        from game.game_state.modifier import (ContinuouosEffectModifierSource,
            CounterModifierSource, AttachedModifierSource)
        from game.game_actions.data_structs.ability import CastSpellAbilityDefinition
        from game.cards.starter_cards import IMPASSIONED_ORATOR
        from game.stat_type import STAT_KEYWORDS, STAT_COLORS
        if type(card) is not Card or card.state.face_down:
            return None
        runtime = card.state
        if (card.attached_to is not None or runtime.attached or runtime.active_cont_effects
                or runtime._anchored_modifiers or runtime.skip_untap is not None
                or tuple(map(type, card.modifier_sources)) != (
                    ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource)):
            return None
        definition = card.definition
        # The Orator template observes another entering friendly creature and
        # gains life; it has no identity-specific combat decision. Other custom
        # triggers require their own reviewed adapter instead of name matching.
        if (definition.triggers and definition != IMPASSIONED_ORATOR
                or definition.continuous_effects or definition.replacement_effects
                or definition.enchant is not None or definition.attach_mods):
            return None
        abilities = tuple(card.get_ability_defs(self.state).values())
        if any(type(a) is not CastSpellAbilityDefinition for a in abilities):
            return None
        if tuple(card.get_trigger_defs(self.state).values()) != tuple(card._keyed_definitions(definition.triggers, 'trigger').values()):
            return None
        counters = tuple(sorted(runtime.counters.items()))
        if any(type(k) is not str or type(v) is not int for k, v in counters):
            return None
        self.fact_reads += 1
        definition_class = next((i for i, known in enumerate(self.definitions) if known == definition), None)
        if definition_class is None:
            definition_class = len(self.definitions)
            self.definitions.append(definition)
        entered = runtime.zone_info.entered_at
        return CardRoleSignature((
            definition_class, card.owner, card.get_controller(self.state),
            frozenset(card.get_types(self.state)), frozenset(card.get_subtypes(self.state)),
            frozenset(card.get_stat(STAT_COLORS, self.state)),
            frozenset(card.get_stat(STAT_KEYWORDS, self.state)),
            card.get_power(self.state), card.get_toughness(self.state),
            card.is_tapped, runtime.damage_marked, runtime.damage_by_deathtouch,
            counters, card.zone_revision, card.controlled_since,
            entered.turn_number, entered.phase, runtime.mana_x,
            card.is_token, card.token_left_battlefield, card.loyalty_activated_turn))

    def groups(self, cards, extra=None):
        """!
        @brief Preserve stable concrete order while interning supported role groups.
        """
        rows, indices = [], {}
        for card in sorted(cards, key=lambda c: str(c.command_id)):
            signature = self.signature(card)
            key = (signature, extra(card) if extra else None) if signature is not None else ('singleton', card)
            if key not in indices:
                indices[key] = len(rows)
                rows.append([])
            rows[indices[key]].append(card)
        return tuple(tuple(row) for row in rows)


class CombatDeclarationEquivalence:
    """!
    @brief Decision-local certificate and count generators for native combat.

    This models exchangeable declarations only. Opaque future rules or different
    memories can still distinguish concrete actions, so full continuation remains.
    """
    scope = 'public-combat-declarations-v1'

    def __init__(self, request, *, blockers):
        from game.rules.combat import CombatState
        from game.game_actions.generation.decision_abstraction.selection_strategies import combat_view
        self.request, self.blockers = request, blockers
        self.roles = CombatRoles(request.state)
        self.valid = (type(request.state.combat) is CombatState
                      and len(request.state.active_players) == 2
                      and request.state.stack.is_empty())
        self.attacker_groups = self.blocker_groups = self.targets = ()
        self.reduced = False
        if not self.valid:
            return
        state, player = request.state, request.player
        if blockers:
            combat = combat_view(state)
            self.attacker_groups = self.roles.groups(combat.attackers,
                extra=lambda c: (combat.attackers[c], c in combat.blocked))
            # Native pair admissibility is part of the grouping contract, even
            # if a custom card happens to share the same public characteristics.
            objects = tuple(dict.fromkeys(b for group in self.attacker_groups
                for b in combat.legal_blockers(player, group[0])))
            self.blocker_groups = self.roles.groups(objects, extra=lambda b: tuple(
                combat.blocker_error(b, group[0], player) is None for group in self.attacker_groups))
            self.allowed = tuple(tuple(combat.blocker_error(bg[0], ag[0], player) is None
                                       for bg in self.blocker_groups) for ag in self.attacker_groups)
        else:
            self.attacker_groups = self.roles.groups(state.combat.legal_attackers(player))
            from game.enums import CardType, ZoneType
            from helper.query_system.query import EqQuery, InQuery
            from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_CONTROLLER
            opponents = tuple(p for p in state.active_players if p is not player)
            walkers = state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                & EqQuery(IK_TYPE, CardType.PLANESWALKER) & InQuery(IK_CONTROLLER, frozenset(opponents)))
            self.targets = (*opponents, *sorted(walkers, key=lambda c: str(c.command_id)))
        self.reduced = any(len(g) > 1 for g in (*self.attacker_groups, *self.blocker_groups))
        self.blocker_index = {b: i for i, group in enumerate(self.blocker_groups) for b in group}

    def action_class(self, mapping):
        """!
        @brief Canonicalize one concrete declaration and retain its exact orbit mass.
        """
        if not self.blockers:
            key = tuple(tuple(sum(mapping.get(a) is target for a in group) for target in self.targets)
                        for group in self.attacker_groups)
            return self.attack_binding(key)
        rows = {a: [0] * len(self.blocker_groups) for group in self.attacker_groups for a in group}
        for blocker, attacker in mapping.items():
            rows[attacker][self.blocker_index[blocker]] += 1
        key = tuple(tuple(sorted((tuple(rows[a]) for a in group), reverse=True)) for group in self.attacker_groups)
        return self.block_binding(key)

    def attack_binding(self, key):
        """!
        @brief Bind counts to stable representatives without enumerating subsets.
        """
        binding, mass = [], 1
        for group, counts in zip(self.attacker_groups, key):
            offset, denominator = 0, factorial(len(group) - sum(counts))
            for target, count in zip(self.targets, counts):
                binding.extend((card, target) for card in group[offset:offset + count])
                offset += count
                denominator *= factorial(count)
            mass *= factorial(len(group)) // denominator
        return CombatActionClass(key, mass, tuple(binding))

    def block_binding(self, key):
        """!
        @brief Bind sorted blocker bundles and compute their concrete multiplicity.
        """
        offsets = [0] * len(self.blocker_groups)
        binding, mass, denominator = [], 1, 1
        for group, rows in zip(self.attacker_groups, key):
            repeats = Counter(rows)
            mass *= factorial(len(group)) // self._factorial_product(repeats.values())
            for attacker, counts in zip(group, rows):
                for i, count in enumerate(counts):
                    binding.extend((b, attacker) for b in self.blocker_groups[i][offsets[i]:offsets[i] + count])
                    offsets[i] += count
                    denominator *= factorial(count)
        for i, group in enumerate(self.blocker_groups):
            mass *= factorial(len(group))
            denominator *= factorial(len(group) - offsets[i])
        return CombatActionClass(key, mass // denominator, tuple(binding))

    @staticmethod
    def _factorial_product(counts):
        value = 1
        for count in counts:
            value *= factorial(count)
        return value

    def representatives(self):
        """!
        @brief Enumerate integer counts/bundles directly, never a concrete product.
        """
        if not self.blockers:
            def counts(remaining, targets):
                if not targets:
                    yield ()
                else:
                    for count in range(remaining, -1, -1):
                        for tail in counts(remaining - count, targets - 1):
                            yield (count, *tail)
            def groups(index, prefix):
                if index == len(self.attacker_groups):
                    yield self.attack_binding(tuple(prefix))
                else:
                    for row in counts(len(self.attacker_groups[index]), len(self.targets)):
                        yield from groups(index + 1, (*prefix, row))
            yield from groups(0, ())
            return
        sizes = tuple(len(group) for group in self.blocker_groups)
        def groups(index, remaining, prefix):
            if index == len(self.attacker_groups):
                yield self.block_binding(tuple(prefix))
                return
            def rows(position, left, previous, selected):
                if position == len(self.attacker_groups[index]):
                    yield from groups(index + 1, left, (*prefix, tuple(selected)))
                    return
                ranges = tuple(range(n + 1) if allowed else (0,) for n, allowed in zip(left, self.allowed[index]))
                for row in product(*ranges):
                    if previous is not None and row > previous:
                        continue
                    yield from rows(position + 1, tuple(n - used for n, used in zip(left, row)), row, (*selected, row))
            yield from rows(0, remaining, None, ())
        yield from groups(0, sizes, ())


class EquivalentCombatStrategy:
    """!
    @brief Offer role representatives first, then every remaining concrete action.
    """
    def __init__(self, *, blockers, width, attack_risk=False):
        self.blockers, self.width, self.attack_risk = blockers, width, attack_risk

    def generate(self, request):
        """!
        @brief Keep native legality and eventual concrete completeness unchanged.
        """
        from game.ai.native_candidates import NativeCombatStrategy
        from game.game_actions.generation.decision_abstraction.options import DeclareAttackersOption, DeclareBlockersOption
        from game.game_actions.generation.decision_abstraction.selection_strategies import FullAttackersStrategy, FullBlockersStrategy
        base = NativeCombatStrategy(blockers=self.blockers, width=self.width, attack_risk=self.attack_risk)
        context = CombatDeclarationEquivalence(request, blockers=self.blockers)
        if not context.valid or not context.reduced:
            yield from base.generate(request)
            return
        option_type = DeclareBlockersOption if self.blockers else DeclareAttackersOption
        class_keys, concrete_keys = set(), set()
        def emit(action):
            if action.key not in class_keys:
                class_keys.add(action.key)
                concrete_keys.add(frozenset(action.binding))
                yield option_type(action.bind())
        for option in base.drafts(request, block_targets=(tuple(g[0] for g in context.attacker_groups)
                                                       if self.blockers else None)):
            yield from emit(context.action_class(option.declarations))
        for action in context.representatives():
            yield from emit(action)
        # Future rules/recall can distinguish equal declaration roles. Do not
        # claim a complete native menu until all concrete alternatives exist.
        exhaustive = FullBlockersStrategy() if self.blockers else FullAttackersStrategy()
        for option in exhaustive.generate(request):
            key = frozenset(option.declarations.items())
            if key not in concrete_keys:
                concrete_keys.add(key)
                yield option
