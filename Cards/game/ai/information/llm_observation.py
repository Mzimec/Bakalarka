"""Compact presentation of an already player-filtered observation for an LLM.

This does not inspect State, definitions or hidden cards. It is a presentation
projection, not an MCTS identity or a replacement for the authoritative view.
Current card values stay on card rows; identical current rule descriptions are
shared separately. Single-effect, targetless execution plumbing is redundant
with the effect itself. Nontrivial action plans and target slots are retained.
"""
from copy import deepcopy
import json


CARD_DEFAULTS = {
    'face_down': False, 'identity_visible': True, 'token': False, 'legendary': False,
    'tapped': False, 'counters': {}, 'damage_marked': 0, 'damage_by_deathtouch': False,
    'attached_to': None, 'attachments': [], 'power': None, 'toughness': None,
    'chosen_x': 0, 'summoning_sick': False, 'skip_untap': False,
    'loyalty_activated_this_turn': False, 'keywords': [], 'colors': [], 'subtypes': [],
}
RULE_FIELDS = ('text', 'abilities', 'triggers', 'static_abilities', 'attachment_restrictions')
ABILITY_DEFAULTS = {'mana_ability': False, 'sorcery_speed': False}


def _rules(value):
    """Preserve effect text/data, costs, target bindings and branch/sequence plans."""
    if isinstance(value, list):
        return [_rules(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    features = value.get('features')
    if (value.get('kind') == 'MoveSourceEffect' and isinstance(features, dict)
            and set(features) == {'kind', 'destination'} and features['kind'] == 'move_source'
            and value.get('text') == f"Move this card to {features['destination']}."
            and not value.get('description_unavailable') and not value.get('semantic')):
        # Native generated stack/zone plumbing, expressed as the exact movement.
        return {'key': value.get('key'), 'move_source': features['destination']}
    result = {}
    for key, item in value.items():
        # Empty optional declarative containers carry no extra rules.
        if item is None or item == [] or item == {}:
            continue
        if key == 'description_unavailable' and item is False:
            continue
        if key == 'features' and item == {'kind': 'unknown'}:
            continue  # Keep the actual effect text and any unknown-description flag.
        result[key] = _rules(item)
    if 'uses_stack' in value and 'allowed_zones' in value:
        for key, default in ABILITY_DEFAULTS.items():
            if value.get(key) == default:
                result.pop(key, None)
    if value.get('kind') == 'SubAbilityDefinition':
        plan = value.get('action_node', {})
        effects = value.get('effects', [])
        # Only one exact effect with no target slots: no ordering/branch decision.
        if (isinstance(plan, dict) and plan.get('kind') == 'EffectActionNode'
                and set(plan) == {'kind', 'effect_map'}
                and len(effects) == 1 and isinstance(effects[0], dict)
                and plan.get('effect_map') == {effects[0].get('key'): []}
                and not value.get('slots')):
            result.pop('action_node', None)
            result['effects'][0].pop('key', None)
            if set(result) == {'kind', 'effects'}:
                return result['effects'][0]
        result.pop('kind', None)
    return result


def compact_observation(view):
    """Return detached compact facts; never substitute printed rules for live stats."""
    if not isinstance(view, dict):
        raise ValueError('Compact LLM observation requires a detached dict')
    result = deepcopy(view)
    catalog, rule_ids, visible = {}, {}, {}
    for zone in ('hand', 'battlefield', 'graveyard', 'exile', 'stack'):
        rows = []
        for original in view.get(zone, []):
            if not isinstance(original, dict):
                rows.append(deepcopy(original))
                continue
            row = {key: deepcopy(item) for key, item in original.items()
                   if key not in RULE_FIELDS and
                   (key not in CARD_DEFAULTS or item != CARD_DEFAULTS[key])}
            rules = {key: _rules(original[key]) for key in RULE_FIELDS
                     if original.get(key) not in (None, [], {}, '')}
            if rules:
                canonical = json.dumps(rules, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                rule_id = rule_ids.get(canonical)
                if rule_id is None:
                    rule_id = f'r{len(catalog)}'
                    rule_ids[canonical] = rule_id
                    catalog[rule_id] = rules
                row['rules_ref'] = rule_id
            rows.append(row)
            if original.get('identity_visible', True) and original.get('id') is not None:
                visible[original['id']] = original
        result[zone] = rows
    # Identity/location facts already explicit on visible rows need not be repeated.
    knowledge = result.get('knowledge')
    if isinstance(knowledge, dict):
        for field in ('observed_cards', 'known_locations'):
            kept = []
            for fact in knowledge.get(field, []):
                current = visible.get(fact.get('card_id')) if isinstance(fact, dict) else None
                same = (current is not None and current.get('name') == fact.get('name')
                        and current.get('owner') == fact.get('owner'))
                if field == 'known_locations':
                    same = same and current.get('zone') == fact.get('zone')
                if not same:
                    kept.append(fact)
            knowledge[field] = kept
        events = knowledge.get('recent_events')
        if (isinstance(events, list) and events and all(isinstance(event, dict) for event in events)
                and all(set(event) == set(events[0]) for event in events)):
            # Exact ordered table: no events, fields, nulls or identities are lost.
            columns = list(events[0])
            knowledge['recent_events'] = {
                'columns': columns, 'rows': [[event[key] for key in columns] for event in events]}
    result['pending_abilities'] = _rules(view.get('pending_abilities', []))
    result['continuous_effects'] = _rules(view.get('continuous_effects', []))
    result['replacement_effects'] = _rules(view.get('replacement_effects', []))
    result['rule_catalog'] = catalog
    result['card_defaults'] = deepcopy(CARD_DEFAULTS)
    result['ability_defaults'] = deepcopy(ABILITY_DEFAULTS)
    result['llm_view_version'] = 1
    return result
