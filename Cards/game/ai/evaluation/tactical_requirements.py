"""Immutable rule requirements with explicit logic, quantities and local facts.

Only detached semantic trees and actor observations are read. An assessment is
about the stated visible prerequisite, never a replacement for native legality,
mana-source planning, stack resolution or a proof of an executable combo.
"""
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from math import isfinite

from game.ai.evaluation.semantic_encoding import SemanticNode
from game.ai.evaluation.card_mechanics import _signal
from game.ai.evaluation.tactical_context import CardReference, PlayerReference


@dataclass(frozen=True, slots=True)
class Requirement:
    """One node of an AND/OR/ordered requirement tree; facts are detached scalars."""
    kind: str
    phase: str = 'condition'
    subject: str = 'source'
    facts: tuple = ()
    children: tuple = ()
    semantic: SemanticNode | None = None

    def __post_init__(self):
        if (type(self.kind) is not str or type(self.phase) is not str or type(self.subject) is not str
                or type(self.facts) is not tuple or type(self.children) is not tuple
                or any(type(c) is not Requirement for c in self.children)
                or self.semantic is not None and type(self.semantic) is not SemanticNode):
            raise TypeError('Requirements contain immutable detached facts only')
        for fact in self.facts:
            if (type(fact) is not tuple or len(fact)!=2 or type(fact[0]) is not str
                    or fact[1] is not None and type(fact[1]) not in (str,int,float,bool)
                    or type(fact[1]) is float and not isfinite(fact[1])):
                raise TypeError('Requirement facts must be finite detached scalars')


def requirement(kind, children=(), *, phase='condition', subject='source', semantic=None, **facts):
    return Requirement(kind, phase, subject, tuple(sorted(facts.items())), tuple(children), semantic)


@dataclass(frozen=True, slots=True)
class RequirementAssessment:
    requirement: Requirement
    status: str = 'unknown'
    available: float | None = None
    subjects: tuple = ()
    children: tuple = ()

    def __post_init__(self):
        if (type(self.requirement) is not Requirement or self.status not in ('satisfied','unsatisfied','unknown')
                or type(self.children) is not tuple or any(type(c) is not RequirementAssessment for c in self.children)
                or type(self.subjects) is not tuple
                or any(type(s) not in (CardReference,PlayerReference) for s in self.subjects)
                or self.available is not None and (type(self.available) not in (int,float) or not isfinite(self.available))):
            raise TypeError('Requirement assessment must contain detached references and finite facts')


def account_resources(check):
    """Do not certify an AND by independently spending the same visible resource."""
    if check.status != 'satisfied' or check.requirement.kind not in ('all_of','sequence'):
        return check
    spent, life, available_life = Counter(), Counter(), {}
    ordered = []
    pools, ambiguous = [], False
    def visit(current):
        nonlocal ambiguous
        req, attrs = current.requirement, dict(current.requirement.facts)
        if req.kind == 'any_of':
            viable = [c for c in current.children if c.status != 'unsatisfied']
            if len(viable)==1:
                visit(viable[0])
            elif any(c.requirement.phase=='cost' for c in viable):
                ambiguous = True
            return
        if req.kind == 'mana':
            if not attrs.get('committed'):
                pools.append(current)
            return
        if req.phase == 'cost' and req.kind in ('tap','sacrifice'):
            spent.update((req.kind,ref) for ref in current.subjects)
            ordered.extend((req.kind,ref) for ref in current.subjects)
        elif req.phase == 'cost' and req.kind == 'life' and type(attrs.get('amount')) in (int,float):
            for ref in current.subjects:
                life[ref] += attrs['amount']
                if current.available is not None:
                    available_life[ref] = current.available
        for child in current.children:
            visit(child)
    for child in check.children:
        visit(child)
    status = check.status
    if ambiguous:
        status = 'unknown'
    elif any(n>1 for n in spent.values()) or any(n>available_life.get(ref,float('inf')) for ref,n in life.items()):
        status = 'unsatisfied'
    elif check.requirement.kind=='sequence':
        removed = set()
        for kind,ref in ordered:
            if kind=='tap' and ref in removed:
                status = 'unsatisfied'
            if kind=='sacrifice':
                removed.add(ref)
        if len(pools)>1 and status=='satisfied':
            status = 'unknown'
    elif len(pools)>1 or any(('tap',ref) in spent and ('sacrifice',ref) in spent for _,ref in spent):
        # Tap-then-sacrifice may work, sacrifice-then-tap may not. A generic
        # conjunction does not prove a legal execution order.
        status = 'unknown'
    return RequirementAssessment(check.requirement,status,check.available,check.subjects,check.children)


def _parse(tree, phase):
    attrs, kind = dict(tree.attributes), tree.kind
    if kind == 'one_of':
        return requirement('any_of', (_parse(c, phase) for c in tree.children), phase=phase)
    if kind == 'variable':
        return requirement('variable', (_parse(c,'cost' if i==0 else 'effect') for i,c in enumerate(tree.children)),
                           phase=phase,semantic=tree,**attrs)
    if kind in ('all_of', 'sequence', 'cost', 'resolution', 'subability', 'binding'):
        return requirement('sequence' if kind == 'sequence' else 'all_of',
                           (_parse(c, phase) for c in tree.children), phase=phase)
    if kind == 'mana':
        return requirement('mana', (_parse(c, phase) for c in tree.children), phase=phase)
    if kind in ('generic_mana', 'colored_mana', 'mana_requirement', 'hybrid_generic_mana', 'phyrexian_mana', 'variable_mana'):
        return requirement('mana_unit', (_parse(c, phase) for c in tree.children), phase=phase, subject='controller',
                           semantic=tree, resource=kind, **attrs)
    if kind == 'color':
        return requirement('color', phase=phase, subject='controller', **attrs)
    if kind in ('slot', 'distinct', 'slot_reference'):
        return requirement(kind, (_parse(c, phase) for c in tree.children), phase=phase, subject='target', semantic=tree, **attrs)
    if kind in ('target', 'card_target', 'player_target', 'self_target', 'spell_target', 'any_damage_target'):
        return requirement('target', (_parse(c, phase) for c in tree.children), phase=phase, subject='target', semantic=tree, **attrs)
    if kind in ('and_query', 'or_query'):
        return requirement('any_of' if kind == 'or_query' else 'all_of', (_parse(c, phase) for c in tree.children),
                           phase=phase, subject='target')
    if kind in ('equals', 'in_query', 'query_value'):
        return requirement('predicate', (_parse(c, phase) for c in tree.children), phase=phase, subject='target', semantic=tree, **attrs)
    if kind == 'unknown':
        return requirement('unknown', phase=phase, semantic=tree, **attrs)
    if kind == 'empty':
        return requirement('all_of', phase=phase)
    if phase == 'cost':
        if kind in ('tap', 'sacrifice', 'move_zone'):
            return requirement(kind, phase=phase, subject='source' if attrs.get('recipient') == 'source' else 'target',
                               semantic=tree, **attrs)
        return requirement('unknown_cost', phase=phase, semantic=tree, effect=kind)
    return requirement('effect', phase=phase, semantic=tree, effect=kind, **attrs)


@lru_cache(maxsize=512)
def ability_requirements(ability):
    """Keep native cost alternatives, trigger qualifiers and target cardinality."""
    attrs = dict(ability.semantic.attributes)
    children = []
    if ability.active_zones:
        children.append(requirement('any_of', (requirement('zone', zone=z) for z in ability.active_zones)))
    for part in ability.semantic.children:
        if part.kind == 'cost':
            children.append(_parse(part, 'cost'))
        elif part.kind == 'trigger':
            attrs = dict(part.attributes)
            if 'phase' in attrs:
                attrs['step'] = attrs.pop('phase')
            children.append(requirement('event', semantic=part, **attrs))
        elif part.kind == 'resolution':
            # Effects are potential outcomes, not preconditions to having them.
            # Target constraints nevertheless belong to announcement/choice.
            def targets(tree):
                if tree.kind == 'slot':
                    yield _parse(tree, 'condition')
                elif tree.kind != 'create_token':
                    for child in tree.children:
                        yield from targets(child)
            children.extend(targets(part))
        elif part.kind == 'variable':
            children.append(_parse(part, 'cost'))
        elif part.kind == 'unknown':
            children.append(_parse(part, 'condition'))
    if attrs.get('spell'):
        children.append(requirement('cast_spell'))
    if attrs.get('sorcery_speed'):
        children.append(requirement('timing', timing='sorcery'))
    if ability.path == ('keyword', 'lifelink'):
        children.append(requirement('positive_damage', amount=1))
    if ability.path == ('native', 'enter'):
        children.append(requirement('move_zone', destination='BATTLEFIELD'))
    if ability.path == ('native', 'cast'):
        children.append(requirement('cast_spell'))
    return requirement('all_of', children)


@lru_cache(maxsize=512)
def event_guard(ability, event):
    """Require the branch which produces this event, not an unrelated OR branch."""
    def visit(tree, phase):
        if tree.kind == 'create_token' or _signal(tree):
            return (requirement('event_result', phase=phase, semantic=tree, event=event)
                    if any(s.event == event for s in _signal(tree)) else None)
        found = [(i, visit(c, phase)) for i, c in enumerate(tree.children)]
        found = [(i, child) for i, child in found if child is not None]
        if not found:
            return None
        if tree.kind == 'one_of':
            return requirement('any_of', (requirement('choice', (child,), phase=phase,
                               semantic=tree.children[i]) for i, child in found), phase=phase)
        return requirement('any_of', (child for _, child in found), phase=phase) if len(found) > 1 else found[0][1]
    found = [visit(part, 'cost' if part.kind == 'cost' else 'effect') for part in ability.semantic.children
             if part.kind in ('cost', 'resolution')]
    found = [r for r in found if r is not None]
    if found:
        return requirement('any_of', found)
    return requirement('event_result', event=event, condition='native_or_keyword_event')


def action_costs(detail, data, *, committed=False):
    """Read the selected native effects and exact payment, not printed alternatives."""
    def parsed(index,effect):
        result=_parse(effect.semantic,'cost')
        return Requirement(result.kind,result.phase,result.subject,
            tuple(sorted((*result.facts,('bound_effect',index)))),result.children,result.semantic)
    costs=[(i,e) for i,e in enumerate(detail.effects) if e.phase=='cost']
    children=[parsed(i,e) for i,e in costs if getattr(e,'stage','outer')=='mana_activation']
    mana = (data.get('cost') or {}).get('mana')
    if isinstance(mana, dict):
        units = []
        for token, amount in (mana.get('payment') or {}).get('mapping', ()):
            name = token[-1] if isinstance(token, (list, tuple)) and token else None
            units.append(requirement('mana_unit', phase='cost', subject='controller', amount=amount,
                resource='generic_mana' if name == 'GENERIC' else 'colored_mana', color=name, committed=committed))
        children.append(requirement('mana', units, phase='cost', committed=committed))
        if mana.get('life_payment', 0):
            children.append(requirement('life', phase='cost', subject='controller', amount=mana['life_payment']))
    children.extend(parsed(i,e) for i,e in costs if getattr(e,'stage','outer')!='mana_activation')
    return requirement('sequence', children, phase='cost')


class RequirementEvaluator:
    """Tri-state local assessments, with unactivated mana sources left unknown."""
    def __init__(self, view):
        self.cards = {(str(c['id']), c.get('zone_revision', 0)): c for zone in ('hand', 'battlefield', 'stack', 'graveyard', 'exile')
                      for c in view.get(zone, ()) if c.get('identity_visible', True) and not c.get('face_down')}
        self.players = {p['id']: p for p in view.get('players', ())}

    def card(self, reference):
        return self.cards.get((reference.identity, reference.zone_revision)) if isinstance(reference, CardReference) else None

    def assess(self, root, *, source=None, controller=0, targets=()):
        def visit(req):
            attrs = dict(req.facts)
            subjects = (PlayerReference(controller),) if req.subject == 'controller' else targets if req.subject == 'target' else (source,) if source else ()
            children = tuple(visit(c) for c in req.children)
            status, available = 'unknown', None
            states = tuple(c.status for c in children)
            if req.kind in ('all_of', 'sequence', 'slot', 'variable'):
                status = 'unsatisfied' if 'unsatisfied' in states else 'satisfied' if all(s == 'satisfied' for s in states) else 'unknown'
                if req.kind == 'variable':
                    status = 'unknown'
            elif req.kind == 'any_of':
                status = 'satisfied' if 'satisfied' in states else 'unsatisfied' if states and all(s == 'unsatisfied' for s in states) else 'unknown'
            elif req.kind == 'zone':
                card = self.card(source)
                if card is not None:
                    status = 'satisfied' if card.get('zone') == attrs['zone'] else 'unsatisfied'
            elif req.kind == 'tap':
                cards = tuple(self.card(ref) for ref in subjects)
                if cards and all(c is not None for c in cards):
                    if any(c.get('zone') != 'BATTLEFIELD' or c.get('tapped') or
                           'CREATURE' in c.get('types', ()) and c.get('summoning_sick') is True for c in cards):
                        status = 'unsatisfied'
                    elif all('tapped' in c and ('CREATURE' not in c.get('types', ()) or c.get('summoning_sick') is False) for c in cards):
                        status = 'satisfied'
            elif req.kind == 'sacrifice':
                cards = tuple(self.card(ref) for ref in subjects)
                if cards and all(c is not None for c in cards):
                    status = 'satisfied' if all(c.get('zone') == 'BATTLEFIELD' and c.get('controller') == controller for c in cards) else 'unsatisfied'
                    available = float(len(cards))
            elif req.kind == 'life':
                player = self.players.get(controller)
                if player is not None:
                    available = player.get('life')
                    if available is not None:
                        status = 'satisfied' if available >= attrs['amount'] else 'unsatisfied'
            elif req.kind == 'mana_unit':
                pool = self.players.get(controller, {}).get('mana')
                if pool is not None:
                    colors = [dict(c.facts).get('value') for c in req.children if c.kind == 'color']
                    color = attrs.get('color', colors[0] if len(colors) == 1 else None)
                    available = float(sum(pool.values()) if attrs.get('resource') == 'generic_mana' else pool.get(color, 0))
                    amount = attrs.get('amount')
                    if type(amount) in (int, float) and available >= amount and attrs.get('resource') in ('generic_mana', 'colored_mana'):
                        status = 'satisfied'
                if attrs.get('committed'):
                    status = 'satisfied'  # This payment came from the offered native action.
            elif req.kind == 'mana':
                if attrs.get('committed'):
                    status = 'satisfied'
                else:
                    pool = self.players.get(controller, {}).get('mana')
                    required, generic, fixed = Counter(), 0, True
                    for child in req.children:
                        a = dict(child.facts)
                        colors = [dict(c.facts).get('value') for c in child.children if c.kind == 'color']
                        amount = a.get('amount')
                        if type(amount) not in (int, float):
                            fixed = False
                        elif a.get('resource') == 'generic_mana':
                            generic += amount
                        elif a.get('resource') == 'colored_mana' and len(colors) == 1:
                            required[colors[0]] += amount
                        else:
                            fixed = False
                    if fixed and pool is not None and all(pool.get(c, 0) >= n for c, n in required.items()) and sum(pool.values()) >= sum(required.values())+generic:
                        status = 'satisfied'
            elif req.kind == 'color':
                status = 'satisfied'  # A literal parameter, not an outstanding condition.
            return account_resources(RequirementAssessment(req,status,available,tuple(s for s in subjects if s is not None),children))
        return visit(root)
