"""World-owned rule fields with reusable dependency checks and typed identities.

Frozen definitions can contain mutable maps and runtime references. Hits check
those inputs without rebuilding a signature tree or rendering/hashing the output.
"""
from collections import Counter, OrderedDict
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

from game.ai.information.structured_values import (ValueStore, structural_digest)
from game.ai.information.visible_effects import effect_features
from game.game_state import Card, Player
from game.game_actions.data_structs.effect import Effect
from game.game_actions.resolution.event_bus import GameEvent, CardLastKnownInformation
from game.mana.mana_value import ManaValueBase, format_mana_cost
from helper.mutability_objs import ImmutableSet
from helper.static_definition import is_static_definition


def mapping_key(value):
    return (str(getattr(value, 'name', value))
            if isinstance(value, (str, int, Enum)) or hasattr(value, 'name')
            else type(value).__name__)


def effect_inputs(value):
    key, text, features = value.key, value.get_info(), effect_features(value)
    # Python equality merges False/0 and signed zero; observation identities do
    # not. Features are a small live leaf, not the whole ability description.
    return type(key), key, type(text), text, structural_digest(features)


class Dependencies:
    """Dependencies of one field, including visibility and mutable backing maps."""
    def __init__(self):
        self.checks = []
        self.seen = {}
        self.permissions = ()

    def matches(self, builder):
        for kind, source, expected in self.checks:
            if kind == 'type':
                if type(source) is not expected:
                    return False
            elif kind == 'attrs':
                for name, value in expected:
                    if getattr(source, name) is not value:
                        return False
            elif kind == 'mapping':
                if len(source) != len(expected):
                    return False
                for (key, value), (old_key, old_value) in zip(source.items(), expected):
                    if key is not old_key or value is not old_value:
                        return False
            elif kind == 'mapping_key':
                if mapping_key(source) != expected:
                    return False
            elif kind == 'sequence':
                if len(source) != len(expected):
                    return False
                for value, old_value in zip(source, expected):
                    if value is not old_value:
                        return False
            elif kind == 'reference':
                if builder.reference(source) != expected:
                    return False
            elif kind == 'lki':
                if builder.knows_last_known(source) != expected:
                    return False
            elif kind == 'mana':
                if format_mana_cost(source) != expected:
                    return False
            elif kind == 'effect':
                # Extensible text/features remain live inputs.
                if effect_inputs(source) != expected:
                    return False
            elif kind == 'dataclass':
                if fields(source) != expected:
                    return False
        return True

    def attrs(self, source, names):
        values = tuple((name, getattr(source, name)) for name in names)
        self.checks.append(('attrs', source, values))
        return values

    def watch(self, builder, value, depth=0):
        if value is None or type(value) in (str, int, float, bool):
            return
        if isinstance(value, Enum):
            self.attrs(value, ('name',))
            return
        identity = id(value)
        previous_depth = self.seen.get(identity)
        if previous_depth is not None and previous_depth <= depth:
            return
        # A shallower visit covers every dependency a deeper visit can read.
        # Revisiting at a shallower depth must still extend the existing plan.
        self.seen[identity] = depth
        self.checks.append(('type', value, type(value)))
        if isinstance(value, CardLastKnownInformation):
            visible = builder.knows_last_known(value)
            self.checks.append(('lki', value, visible))
            if not visible:
                return
        if isinstance(value, GameEvent):
            for _, item in self.attrs(value, ('key', 'source', 'controller')):
                self.watch(builder, item)
            return
        if isinstance(value, (Card, Player)):
            self.checks.append(('reference', value, builder.reference(value)))
            return
        if isinstance(value, ManaValueBase):
            self.checks.append(('mana', value, format_mana_cost(value)))
            return
        if isinstance(value, Effect):
            self.checks.append(('effect', value, effect_inputs(value)))
            return
        if depth >= 10:
            return
        if isinstance(value, Mapping):
            items = tuple(value.items())
            self.checks.append(('mapping', value, items))
            references = any(isinstance(k, (Card, Player)) for k, _ in items)
            for k, v in items:
                if references:
                    self.watch(builder, k, depth + 1)
                elif type(k) not in (str, int):
                    self.checks.append(('mapping_key', k, mapping_key(k)))
                self.watch(builder, v, depth + 1)
            return
        if isinstance(value, (tuple, list, set, frozenset)):
            items = tuple(value)
            if type(value) not in (tuple, frozenset, ImmutableSet):
                self.checks.append(('sequence', value, items))
            for item in items:
                self.watch(builder, item, depth + 1)
            return
        if is_dataclass(value) and not isinstance(value, type):
            schema = fields(value)
            self.checks.append(('dataclass', value, schema))
            for _, item in self.attrs(value, (f.name for f in schema if not f.name.startswith('_'))):
                self.watch(builder, item, depth + 1)


class RuntimeFieldCache:
    """Bounded derived fields in one runtime; copying starts a cold cache.

Validation results last for one synchronous viewer lease. Field entries may
survive mutations/rollback because their dependencies are checked next time.
The immutable result contains no runtime object references.
"""
    def __init__(self, limit=256):
        self.limit = limit
        self.entries = OrderedDict()
        self.counts = Counter()

    def __reduce__(self):
        return type(self), (self.limit,)

    def __deepcopy__(self, memo):
        result = type(self)(self.limit)
        memo[id(self)] = result
        return result

    def retain_definitions(self):
        """Keep published recipes across restore, rechecking hits next lease.

        Published sources contain no world bindings. Discard branch-owned
        descriptions so their dependency plans cannot retain an old runtime.
        """
        for key, entry in tuple(self.entries.items()):
            if not is_static_definition(entry[0]):
                del self.entries[key]

    def ability(self, builder, definition, memo):
        # No nested functions here: even an early hit would allocate their
        # captured cells before it could return the already frozen value.
        return self.read(('ability', id(definition)), definition, builder, memo)

    def describe(self, builder, value, memo):
        if value is None or type(value) in (str, int, float, bool) or isinstance(value, Enum):
            return builder.describe(value)
        return self.read(('description', id(value)), value, builder, memo)

    def read(self, key, source, builder, memo):
        entry = self.entries.get(key)
        if entry is not None and entry[0] is source:
            plan = entry[1]
            validated = id(plan) in memo
            if validated or plan.matches(builder):
                # A card-level projection must also inherit permissions from
                # fields validated earlier in this same viewer lease.
                if validated and plan.permissions and builder._row_reads is not None:
                    for method, subject in plan.permissions:
                        getattr(builder, method)(subject)
                memo[id(plan)] = plan  # Prevent ID reuse during the lease.
                self.entries.move_to_end(key)
                self.counts['hits'] += 1
                return entry[2]
        return self._render(key, source, builder, memo)

    def _render(self, key, source, builder, memo):
        """Prepare dependencies and output only after a miss or invalidation."""
        plan = Dependencies()
        if key[0] == 'ability':
            plan.checks.append(('type', source, type(source)))
            for name, value in plan.attrs(source, ('key', 'uses_stack', 'is_mana_ability',
                    'sorcery_speed', 'allowed_zones', 'loyalty_cost',
                    'cost_subdefs', 'action_subdefs', 'subdefs')):
                if name == 'allowed_zones':
                    plan.checks.append(('sequence', value, tuple(value)))
                    for zone in value:
                        plan.attrs(zone, ('name',))
                else:
                    plan.watch(builder, value)
            rendered = builder.ability(source)
        else:
            plan.watch(builder, source)
            rendered = builder.describe(source)
        plan.permissions = tuple(('reference' if kind == 'reference' else 'knows_last_known', source)
            for kind, source, _ in plan.checks if kind in ('reference', 'lki'))
        value = ValueStore(0).freeze(rendered).readonly
        self.counts['renders'] += 1
        if self.limit:
            self.entries[key] = (source, plan, value)
            memo[id(plan)] = plan
            self.entries.move_to_end(key)
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
        return value
