"""Schema 4 preserves typed requirements and per-action tactical argument trees.

Local parent indices bind the forest; they never become learned identities.
Every node has explicit kind/phase/qualifiers/quantity/visible assessment. Logical
operators are distinct, including mutually exclusive alternatives and sequence.
"""
from dataclasses import dataclass
from typing import ClassVar

from game.enums import CardType, CounterType, ZoneType, ManaType, TurnPhase, CardSubtype
from game.ai.evaluation.action_tactics import ActionTacticsBuilder
from game.ai.training.deck_features import DeckFeatureEncoder, DeckEntityState, MECHANICS, STATUSES
from game.ai.training.entity_features import EntityAction, EntityArgument, ARGUMENT_SIZE, normalized, hot

REQUIREMENT_KINDS = ('all_of', 'any_of', 'sequence', 'variable', 'mana', 'mana_unit', 'color', 'tap', 'sacrifice',
    'life', 'move_zone', 'slot', 'slot_reference', 'distinct', 'target', 'predicate', 'unknown', 'unknown_cost',
    'zone', 'event', 'cast_spell', 'timing', 'positive_damage', 'event_result', 'choice', 'can_attack', 'effect')
PHASES = ('cost', 'effect', 'condition')
SUBJECTS = ('source', 'controller', 'target')
ASSESSMENTS = ('satisfied', 'unsatisfied', 'unknown')
CONTEXT_KINDS = ('costs', 'opportunity', 'produces_event', 'develops_source', 'spends_source', 'affects_source', 'protects_source', 'requirement')
EVENTS = (*MECHANICS, 'attacker_declared', 'attackers_declared', 'attacker_blocked', 'phase_started', 'card_untapped', 'life_lost')
FACT_VALUES = {
    'resource':('generic_mana', 'colored_mana', 'mana_requirement', 'hybrid_generic_mana', 'phyrexian_mana', 'variable_mana'),
    'color':tuple(m.name for m in ManaType), 'value':tuple(dict.fromkeys(x.name for e in (ManaType,ZoneType,CardType,CardSubtype,CounterType) for x in e)),
    'zone':tuple(z.name for z in ZoneType), 'origin':tuple(z.name for z in ZoneType), 'destination':tuple(z.name for z in ZoneType),
    'card_type':tuple(t.name for t in CardType), 'counter_type':tuple(t.name for t in CounterType), 'event':EVENTS,
    'field':('type', 'subtype', 'power', 'toughness', 'zone', 'tapped', 'is_token', 'cmc'), 'timing':('sorcery',),
    'step':tuple(s.name for s in TurnPhase),
}
FACT_NUMBERS = ('amount', 'minimum', 'maximum', 'power_at_most', 'source_only', 'another', 'controlled_only',
                'combat_only', 'to_player_only', 'repeatable', 'committed', 'excludes_source', 'index', 'source', 'target', 'value')
REQUIREMENT_SIZE = (len(REQUIREMENT_KINDS)+1+len(REQUIREMENT_KINDS)+1+len(PHASES)+len(SUBJECTS)
    +len(ASSESSMENTS)+2*len(FACT_NUMBERS)+sum(len(v)+1 for v in FACT_VALUES.values())+5)
CONTEXT_SIZE = len(CONTEXT_KINDS)+len(EVENTS)+len(STATUSES)+REQUIREMENT_SIZE
TACTICAL_ARGUMENT_SIZE = ARGUMENT_SIZE+CONTEXT_SIZE


@dataclass(frozen=True)
class TacticalEntityState(DeckEntityState):
    schema_version: ClassVar[int] = 4
    relation_width: ClassVar[int] = DeckEntityState.relation_width+CONTEXT_SIZE
    feature_version: int = 4


@dataclass(frozen=True)
class TacticalEntityAction(EntityAction):
    argument_width: ClassVar[int] = TACTICAL_ARGUMENT_SIZE
    argument_parents: tuple = ()

    def validate(self, state):
        if state.feature_version != 4:
            raise ValueError('Tactical actions require state schema 4')
        super().validate(state)
        if type(self.argument_parents) is not tuple or len(self.argument_parents) != len(self.arguments):
            raise ValueError('Tactical action requires one parent per argument')
        depths = []
        for index, parent in enumerate(self.argument_parents):
            if type(parent) is not int or not -1 <= parent < index:
                raise ValueError('Argument parent must precede its child in this action')
            depth = 0 if parent < 0 else depths[parent]+1
            if depth > 128:
                raise ValueError('Tactical argument tree exceeds depth bound')
            depths.append(depth)


def requirement_row(check, parent='', depth=0, position=0):
    req, attrs = check.requirement, dict(check.requirement.facts)
    row = [*hot(req.kind, REQUIREMENT_KINDS), req.kind not in REQUIREMENT_KINDS,
           *hot(parent, REQUIREMENT_KINDS), parent not in REQUIREMENT_KINDS,
           *hot(req.phase, PHASES), *hot(req.subject, SUBJECTS), *hot(check.status, ASSESSMENTS)]
    for field in FACT_NUMBERS:
        value = attrs.get(field)
        row.extend((value if type(value) in (int, float, bool) else 0., value is not None))
    for field, vocabulary in FACT_VALUES.items():
        value = attrs.get(field)
        row.extend((*hot(value, vocabulary), value is not None and value not in vocabulary))
    row.extend((depth, position if parent == 'sequence' else 0., len(check.children),
                check.available if check.available is not None else 0., check.available is not None))
    assert len(row) == REQUIREMENT_SIZE
    return normalized(row)


def context_row(kind, mechanic='', status='', check=None, parent='', depth=0, position=0):
    return (*normalized((*hot(kind, CONTEXT_KINDS), *hot(mechanic, EVENTS), *hot(status, STATUSES))),
            *(requirement_row(check, parent, depth, position) if check is not None else (0.,)*REQUIREMENT_SIZE))


class TacticalFeatureEncoder(DeckFeatureEncoder):
    def __init__(self):
        super().__init__()
        self.tactics = ActionTacticsBuilder()

    def tactical_context(self, information):
        if not self.profiles:
            raise ValueError('Bind permitted deck profiles before tactical inference')
        # Base schema 3 consumes the same visible cards/conditional links.
        return self.tactics.base.build(information.current.observation(), self.profiles, information.actions)

    def encode(self, information):
        base_context = self.tactical_context(information)
        base, actions = super().encode(information,tactical_context=base_context)
        view = information.current.observation()
        context = self.tactics.build(view, self.profiles, information.actions,base=base_context)
        refs, index = {}, 0
        for zone in ('hand', 'battlefield', 'stack', 'graveyard', 'exile'):
            for card in view.get(zone, ()):
                from game.ai.evaluation.tactical_context import CardReference
                refs[CardReference(str(card['id']), card.get('zone_revision', 0))] = index
                index += 1
        from game.ai.evaluation.tactical_context import PlayerReference
        for player in sorted(view.get('players', ()), key=lambda p:p['id'] != information.current.player):
            refs[PlayerReference(player['id'])] = index
            index += 1
        relations = [(s,t,(*row, *(0.,)*CONTEXT_SIZE)) for s,t,row in base.relations]
        for link in context.links:
            def relation_nodes(check, parent='', depth=0, position=0):
                relations.append((refs[link.source], refs[link.target],
                    (*(0.,)*DeckEntityState.relation_width, *context_row('opportunity',link.mechanic,link.status,check,parent,depth,position))))
                for i, child in enumerate(check.children):
                    relation_nodes(child,check.requirement.kind,depth+1,i)
            relation_nodes(link.requirements)
        state = TacticalEntityState(base.globals,base.entities,base.semantics,base.groups,tuple(relations))
        result = []
        for original, detail in zip(actions, context.actions):
            arguments = [EntityArgument((*a.features, *(0.,)*CONTEXT_SIZE), a.source, a.target,a.semantics) for a in original.arguments]
            parents = [-1]*len(arguments)
            def add_tree(check, parent=-1, parent_kind='', depth=0, position=0, source=-1, target=-1):
                node_index = len(arguments)
                # Assessed receiver nodes retain their own object binding.
                own_ref = next((refs[r] for r in check.subjects if r in refs), source)
                rows = self._semantic(check.requirement.semantic.to_dict()) if check.requirement.semantic is not None else ()
                arguments.append(EntityArgument((*(0.,)*ARGUMENT_SIZE, *context_row('requirement', check=check,
                    parent=parent_kind,depth=depth,position=position)), own_ref,target,rows))
                parents.append(parent)
                for i, child in enumerate(check.children):
                    add_tree(child,node_index,check.requirement.kind,depth+1,i,source,target)
            root = len(arguments)
            arguments.append(EntityArgument((*(0.,)*ARGUMENT_SIZE,*context_row('costs')),original.source,-1))
            parents.append(-1)
            add_tree(detail.costs,root,source=original.source)
            for relation in detail.relations:
                root = len(arguments)
                source, target = refs.get(relation.source,-1), refs.get(relation.target,-1)
                arguments.append(EntityArgument((*(0.,)*ARGUMENT_SIZE,*context_row(relation.kind,relation.mechanic,relation.status)),source,target))
                parents.append(-1)
                add_tree(relation.requirements,root,source=source,target=target)
            result.append(TacticalEntityAction(original.features,original.source,original.semantics,tuple(arguments),tuple(parents)))
        state.validate()
        for action in result:
            action.validate(state)
        return state, tuple(result)
