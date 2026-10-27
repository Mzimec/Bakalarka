"""Cheap proposal ordering before target products and executable actions exist.

Only individual visible objects/ability definitions and small combat drafts are
scored. Native pipelines still prove legality; no projected menu or JSON is built.
"""
from contextlib import contextmanager


class NativeProposalContext:
    """!
    @brief Reuse visible characteristic facts while constructing native action proposals.
    """
    def __init__(self):
        self.features = {}
        self.facts = {}
        self.restrictions = {}

    def visible(self, card, controller):
        """!
        @brief Check whether an identity may participate in this actor proposal score.
        """
        from game.enums import ZoneType
        return (not card.state.face_down and
                (card.get_zone() in (ZoneType.BATTLEFIELD, ZoneType.STACK, ZoneType.GRAVEYARD, ZoneType.EXILE)
                 or (card.get_zone() == ZoneType.HAND and card.owner is controller)))

    def card_facts(self, card, controller, state):
        """!
        @brief Reuse reviewed characteristics within the current native proposal context.
        """
        key = card, controller
        if key not in self.facts:
            if not self.visible(card, controller):
                self.facts[key] = None
            else:
                self.facts[key] = (
                    max(0, card.get_power(state) or 0), max(0, card.get_toughness(state) or 0),
                    card.get_controller(state), card.state.damage_marked,
                    card.has_keyword(state, 'indestructible'))
        return self.facts[key]

    @contextmanager
    def plan(self, effects):
        """!
        @brief Scope target preferences to the effects currently being generated.
        """
        from game.ai.visible_effects import effect_features
        previous = self.features
        features = {}
        for binding in effects.sequence:
            if not binding.slots:
                continue
            feature = effect_features(binding.effect)
            for slot in binding.slots:
                features.setdefault(id(slot.slot.target_resolver), []).append(feature)
        self.features = features
        try:
            yield
        finally:
            self.features = previous

    def order_targets(self, candidates, source, controller, state, resolver):
        """!
        @brief Rank visible targets without constructing every target combination.
        """
        from game.game_state import Card, Player
        from game.ai.visible_effects import restriction_keywords
        candidates = tuple(candidates)
        if len(candidates) < 2:
            return candidates
        features = self.features.get(id(resolver), ())
        def score(target):
            if isinstance(target, Player):
                sign = 1 if target is not controller else -1
                return sum(sign * (1000 if f.get('amount', 0) >= target.health else .2*f.get('amount', 0))
                           for f in features if f['kind'] == 'damage' and type(f.get('amount')) in (int, float))
            if not isinstance(target, Card):
                return 0
            facts = self.card_facts(target, controller, state)
            if facts is None:
                return 0
            power, toughness, owner, damage, indestructible = facts
            material = 1 + power + toughness
            sign = 1 if owner is not controller else -1
            value, understood = 0., False
            for f in features:
                kind = f['kind']
                if kind == 'damage' and type(f.get('amount')) in (int, float):
                    lethal = f['amount'] >= toughness-damage and not indestructible
                    value += sign * (material if lethal else .05*f['amount'])
                    understood = True
                elif kind in ('destroy', 'counter'):
                    value += sign * (material if kind == 'counter' or not indestructible else 0)
                    understood = True
                elif kind == 'attach' and self.visible(source, controller):
                    if source not in self.restrictions:
                        self.restrictions[source] = restriction_keywords(source.modifiers_to_attach(state))
                    if self.restrictions[source]:
                        value += sign * material
                        understood = True
            return value if understood else power + toughness
        return tuple(sorted(candidates, key=lambda t: (
            -score(t), str(getattr(t, 'command_id', getattr(t, 'idx', ''))))))

    def order_abilities(self, candidates, state, controller):
        """!
        @brief Order ability sources before generating complete executions.
        """
        from game.ai.visible_effects import effect_features
        candidates = tuple(candidates)
        if len(candidates) < 2:
            return candidates
        def score(ability):
            source = ability.source
            facts = self.card_facts(source, controller, state)
            if facts is None:
                return 0
            value = .1*(facts[0]+facts[1])
            for subdef in getattr(ability.definition, 'action_subdefs', ()):
                for effect in subdef.effects:
                    f = effect_features(effect)
                    if f['kind'] == 'draw':
                        value += f['amount']
                    elif f['kind'] == 'damage' and type(f.get('amount')) in (int, float):
                        value += f['amount']
                        if any(p is not controller and f['amount'] >= p.health for p in state.active_players):
                            value += 1000
            return value
        return sorted(candidates, key=lambda a: (-score(a), str(a.source.command_id),
            a.definition.key if self.visible(a.source, controller) else ''))


class NativeCombatStrategy:
    """!
    @brief Yield small drafts before the bounded beam/exhaustive continuation.

    This does not enumerate complete combat products to sort them. The exact
    validator may reject drafts with coupled requirements; enumeration continues.
    """
    def __init__(self, *, blockers, width):
        self.blockers, self.width = blockers, width

    def generate(self, request):
        """!
        @brief Yield native proposals under the configured action-generation policy.
        """
        from game.ai.generation_candidates import BeamCombatStrategy
        from game.game_actions.generation.decision_abstraction.selection_strategies import combat_view
        from game.game_actions.generation.decision_abstraction.options import DeclareAttackersOption, DeclareBlockersOption
        from game.ai.combat_strategies import GreedyBlockersStrategy, CombatEvaluator
        state, player = request.state, request.player
        seen = set()
        def emit(mapping):
            key = frozenset(mapping.items())
            if key not in seen:
                seen.add(key)
                yield (DeclareBlockersOption if self.blockers else DeclareAttackersOption)(mapping)
        if self.blockers:
            combat = combat_view(state)
            for option in GreedyBlockersStrategy().generate(request):
                yield from emit(option.declarations)
            yield from emit({})
            attackers = sorted(combat.attackers, key=lambda c: (-(c.get_power(state) or 0), c.command_id))
            for attacker in attackers:
                blockers = sorted(combat.legal_blockers(player, attacker),
                    key=lambda c: ((c.get_power(state) or 0)+(c.get_toughness(state) or 0), c.command_id))
                count = 2 if attacker.has_keyword(state, 'menace') else 1
                if len(blockers) >= count:
                    yield from emit({b: attacker for b in blockers[:count]})
                power, group = 0, {}
                for blocker in reversed(blockers):
                    group[blocker] = attacker
                    power += max(0, blocker.get_power(state) or 0)
                    if len(group) >= count and power >= (attacker.get_toughness(state) or 0):
                        yield from emit(group)
                        break
        else:
            attackers = sorted(state.combat.legal_attackers(player),
                               key=lambda c: (-(c.get_power(state) or 0), c.command_id))
            evaluator = CombatEvaluator()
            opponents = tuple(p for p in state.active_players if p is not player)
            for opponent in opponents:
                all_attack = {c: opponent for c in attackers if (c.get_power(state) or 0) > 0}
                yield from emit(all_attack)
                defenders = evaluator.defenders(state, opponent)
                yield from emit({c: opponent for c in all_attack if evaluator.safe_attacker(state, c, defenders)})
                yield from emit({})
                # Different sized pressure/defence drafts; no subset product.
                for count in range(1, min(len(attackers), self.width)+1):
                    yield from emit({c: opponent for c in attackers[:count]})
        for option in BeamCombatStrategy(blockers=self.blockers, width=self.width).generate(request):
            yield from emit(option.declarations)
