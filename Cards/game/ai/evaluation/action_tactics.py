"""Action-conditioned tactical relationships and explicit prerequisite trees.

Potential events are matched against the actual selected native effects. No
state is cloned, no effect/predicate is executed and future resolution is never
claimed. Removing a source means risk to future support, not cancellation of
an already-triggered ability or its last-known-information trigger.
"""
from dataclasses import dataclass, replace
from functools import lru_cache

from game.ai.evaluation.card_mechanics import card_profile, AbilityProfile, MechanicSignal, _signal, from_data
from game.ai.evaluation.tactical_context import (TacticalContextBuilder, visible_profile, CardReference, PlayerReference, _references)
from game.ai.evaluation.tactical_requirements import (RequirementEvaluator, ability_requirements, event_guard,
    action_costs, requirement, account_resources)
from game.ai.evaluation.semantic_encoding import node

TACTICAL_VERSION = 1


def compatible_opportunity(producer, consumer, *, same_card, copies=1):
    if producer.event != consumer.event:
        return False
    for field in ('origin', 'destination', 'card_type', 'counter_type'):
        a, b = getattr(producer, field), getattr(consumer, field)
        if a is not None and b is not None and a != b:
            return False
    if consumer.subject == 'source' and producer.subject == 'source' and not same_card:
        return False
    if consumer.subject in ('another', 'another_controlled') and producer.subject == 'source' and same_card and copies < 2:
        return False
    return True


def _combat_profile(profile):
    abilities = []
    for ability in profile.abilities:
        signals = []
        spell = dict(ability.semantic.attributes).get('spell')
        for signal in ability.produces:
            if signal.event == 'card_moved' and signal.subject == 'source':
                origin = signal.origin
                if spell and signal.destination in ('GRAVEYARD','BATTLEFIELD'):
                    origin = 'STACK'
                signals.extend(replace(signal,origin=origin,card_type=t) for t in profile.types or (None,))
            else:
                signals.append(signal)
        abilities.append(replace(ability,produces=tuple(dict.fromkeys(signals))))
    profile = replace(profile,abilities=tuple(abilities))
    if 'CREATURE' not in profile.types:
        return profile
    ability = AbilityProfile(('native', 'combat'), node('empty'),
        produces=(MechanicSignal('attacker_declared', 'source', card_type='CREATURE'),
                  MechanicSignal('attackers_declared', 'source', card_type='CREATURE'),
                  MechanicSignal('damage_dealt', 'source', card_type='CREATURE')),
        requirements=('can_attack', 'combat_event_must_happen'), active_zones=('BATTLEFIELD',), repeatable=True)
    return replace(profile, abilities=(*profile.abilities, ability))


@lru_cache(maxsize=256)
def tactical_card_profile(tree):
    return _combat_profile(card_profile(tree))


def tactical_visible_profile(card):
    return _combat_profile(visible_profile(card))


@dataclass(frozen=True, slots=True)
class TacticalRelation:
    kind: str
    source: object
    target: object
    mechanic: str
    status: str
    requirements: object


@dataclass(frozen=True, slots=True)
class DetailedAction:
    action_index: int
    costs: object
    relations: tuple


@dataclass(frozen=True, slots=True)
class ActionTacticalContext:
    base: object
    links: tuple
    actions: tuple


@dataclass(frozen=True, slots=True)
class BoundEffect:
    phase: str
    semantic: object
    targets: tuple
    source: object
    stage: str = 'outer'


def _ability(card, path):
    return next(a for a in card.profile.abilities if a.path == path)


@lru_cache(maxsize=512)
def _rule(ability, event=None):
    base = ability_requirements(ability)
    if ability.path == ('native', 'combat'):
        extra = (requirement('positive_damage'),) if event == 'damage_dealt' else ()
        return requirement('all_of', (*base.children, requirement('can_attack'), *extra))
    return base


class ActionTacticsBuilder:
    def __init__(self):
        self.base = TacticalContextBuilder(matcher=compatible_opportunity, profile_builder=tactical_visible_profile)

    def build(self, view, profiles=(), actions=(), *, base=None):
        base = self.base.build(view, profiles, actions) if base is None else base
        cards = {c.reference: c for c in base.cards}
        evaluator = RequirementEvaluator(view)
        details = []
        for link in base.connections:
            source, target = cards[link.source], cards[link.target]
            source_ability, target_ability = _ability(source, link.source_ability), _ability(target, link.target_ability)
            src = evaluator.assess(requirement('all_of', (_rule(source_ability, link.mechanic), event_guard(source_ability, link.mechanic))),
                                   source=source.reference, controller=source.controller)
            dst = evaluator.assess(_rule(target_ability), source=target.reference, controller=target.controller)
            # Keep both source/receiver bindings, rather than assessing the
            # receiver's zone or cost using the producer's runtime object.
            combined = replace(src, requirement=requirement('all_of'),
                children=(src, dst), status='unsatisfied' if 'unsatisfied' in (src.status, dst.status) else 'unknown')
            details.append(TacticalRelation('opportunity', source.reference, target.reference,
                                            link.mechanic, link.status, combined))
        detailed_actions = []
        for index, (action, detail) in enumerate(zip(actions, base.actions)):
            data = action.describe() if hasattr(action, 'describe') else action
            controller = (data.get('controller') or {}).get('player', base.player)
            effects = [BoundEffect(e.phase,e.semantic,e.targets,detail.source) for e in detail.effects]
            if dict((data.get('ability_semantic') or {}).get('attributes', ())).get('spell'):
                effects.append(BoundEffect('announcement',node('spell_cast'),(detail.source,),detail.source))
            known = {(c.reference.identity,c.reference.zone_revision):c.reference for c in base.cards}
            for plan in ((data.get('cost') or {}).get('mana') or {}).get('mana_plan', ()):
                sources = tuple(_references(plan.get('source'),known))
                if sources:
                    effects.extend(BoundEffect('cost',from_data(e.get('semantic')),sources,sources[0],'mana_activation')
                                   for e in (plan.get('cost') or {}).get('effects', ()))
            scope = replace(detail, effects=tuple(effects))
            cost_tree = action_costs(scope, data, committed=hasattr(action, 'describe'))
            checks = []
            for req in cost_tree.children:
                bound=dict(req.facts).get('bound_effect')
                effect=effects[bound] if bound is not None else None
                checks.append(evaluator.assess(req,source=effect.source if effect else detail.source,
                    controller=controller,targets=effect.targets if effect else ()))
            checks=tuple(checks)
            costs = evaluator.assess(cost_tree,source=detail.source,controller=controller)
            states = tuple(c.status for c in checks)
            costs = replace(costs,children=checks,status='unsatisfied' if 'unsatisfied' in states else
                            'satisfied' if all(s=='satisfied' for s in states) else 'unknown')
            costs = account_resources(costs)
            relations = []
            removed = {ref for e in effects if e.phase == 'cost' and (e.semantic.kind == 'sacrifice' or
                       e.semantic.kind == 'move_zone' and dict(e.semantic.attributes).get('destination') in ('GRAVEYARD','EXILE'))
                       for ref in e.targets}
            played = cards.get(detail.source)
            develops = (played is not None and played.zone=='HAND' and set(played.profile.types) &
                {'CREATURE','ARTIFACT','ENCHANTMENT','LAND','PLANESWALKER'} and any(
                    e.semantic.kind=='move_zone' and dict(e.semantic.attributes).get('destination') in ('STACK','BATTLEFIELD')
                    and detail.source in e.targets for e in detail.effects))
            for j, link in enumerate(base.connections):
                receiver = _ability(cards[link.target],link.target_ability)
                loses_receiver = link.target in removed and 'BATTLEFIELD' in receiver.active_zones and not (
                    {'GRAVEYARD','EXILE'} & set(receiver.active_zones))
                matched = any(effect.source == link.source and not (loses_receiver and effect.phase != 'cost')
                              and self._matches(effect, link, cards, controller) for effect in effects)
                if matched:
                    relations.append(replace(details[j], kind='produces_event'))
                # Either endpoint is the source of an ability: casting the
                # payoff can develop the plan just as casting its enabler can.
                if develops and detail.source in (link.source,link.target) and link.status!='unavailable':
                    relations.append(replace(details[j],kind='develops_source',status='preparable'))
                for effect in effects:
                    attrs = dict(effect.semantic.attributes)
                    consumed = effect.phase == 'cost' and (effect.semantic.kind == 'sacrifice'
                        or effect.semantic.kind == 'move_zone' and attrs.get('destination') in ('GRAVEYARD', 'EXILE'))
                    # A tapped card is spent for tap-based capabilities, not
                    # for every static/triggered ability of that card.
                    tapped = effect.phase == 'cost' and effect.semantic.kind == 'tap'
                    for ref in effect.targets:
                        if ref not in (link.source, link.target):
                            continue
                        ability_path = link.source_ability if ref == link.source else link.target_ability
                        ability = _ability(cards[ref], ability_path)
                        if consumed or tapped and ('tap' in ability.consumes or ability.path == ('native','combat')):
                            relations.append(replace(details[j], kind='spends_source', source=ref,
                                target=link.target if ref == link.source else link.source))
                        if effect.phase == 'effect' and effect.semantic.kind in ('damage', 'destroy', 'counter'):
                            relations.append(replace(details[j], kind='affects_source', source=detail.source, target=ref))
                        if effect.phase == 'effect' and effect.semantic.kind == 'modify_stat':
                            def keys(tree):
                                yield dict(tree.attributes).get('value') if tree.kind == 'keywords' else None
                                for c in tree.children:
                                    yield from keys(c)
                            if {'hexproof', 'indestructible'} & set(keys(effect.semantic)):
                                relations.append(replace(details[j], kind='protects_source', source=detail.source, target=ref))
            detailed_actions.append(DetailedAction(index, costs, tuple(dict.fromkeys(relations))))
        return ActionTacticalContext(base, tuple(details), tuple(detailed_actions))

    @staticmethod
    def _matches(effect, link, cards, controller):
        signals = (MechanicSignal('spell_cast','source'),) if effect.semantic.kind == 'spell_cast' else _signal(effect.semantic)
        for signal in signals:
            if signal.event != link.mechanic:
                continue
            target = cards[link.target]
            trigger = next((dict(n.attributes) for n in _ability(target, link.target_ability).semantic.children if n.kind == 'trigger'), {})
            if trigger.get('combat_only'):
                continue  # These are spell/ability effects, not actual combat damage.
            refs = effect.targets or (None,)
            for ref in refs:
                affected = cards.get(ref)
                # For movement/tap the event source is the changed card. For
                # damage/life the producer remains the source of the event.
                event_source = ref if signal.event in ('card_moved', 'card_tapped') else link.source
                if trigger.get('source_only') and event_source is not None and event_source != link.target:
                    continue
                if trigger.get('another') and event_source == link.target:
                    continue
                event_controller = ref.player if isinstance(ref, PlayerReference) and signal.event == 'life_gained' else affected.controller if affected else controller
                if trigger.get('controlled_only') and event_controller != target.controller:
                    continue
                if trigger.get('to_player_only') and not isinstance(ref, PlayerReference):
                    continue
                if trigger.get('origin') is not None and affected and affected.zone != trigger['origin']:
                    continue
                if trigger.get('destination') is not None and signal.destination != trigger['destination']:
                    continue
                if trigger.get('counter_type') is not None and signal.counter_type != trigger['counter_type']:
                    continue
                if trigger.get('card_type') is not None and affected and trigger['card_type'] not in affected.profile.types:
                    continue
                if trigger.get('power_at_most') is not None and signal.event != 'card_moved' and affected and affected.profile.power is not None and affected.profile.power > trigger['power_at_most']:
                    continue
                return True
        return False
