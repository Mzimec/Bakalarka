"""Detached native ratings and value reuse over exact observation inputs.

No frozen copies of a live observation are prepared for future evaluation.
Four reviewed state-independent action kinds defer ratings. Value reuse uses
already computed material digests and checks the evaluator implementation.
"""
from collections.abc import Sequence

from game.ai.evaluation.state_value import (StateValueEvaluator, _NATIVE_VALUE_CONTRACT)
from game.ai.evaluation.observation_policy import (_Scorer, ActionRating, _NATIVE_RATE_CONTRACT)
from game.ai.decisions.search_actions import PlayerAction


_PURE_KINDS = frozenset(('PassPriorityAction', 'ConcedeAction', 'LandPlayAction', 'ManaAbilityAction'))


def contract_matches(function, contract):
    native, code, defaults, kwdefaults = contract
    # Coarse benchmark timers already publish this explicit passthrough proof
    # for rule readers. Preserve value/rating admission under the same reviewed
    # timers without retaining their closures in a runtime product or cache.
    actual = getattr(function, '__runtime_rule_passthrough__', function)
    return (actual is native and native.__code__ is code
            and native.__defaults__ is defaults and native.__kwdefaults__ is kwdefaults)


def prepared_score(view, session, evaluator, *, material_key=None):
    functions = (StateValueEvaluator.evaluate, StateValueEvaluator.permanent_value)
    if not all(contract_matches(fn, proof) for fn, proof in zip(functions, _NATIVE_VALUE_CONTRACT)):
        session.counts['actor_value_live_fallbacks'] += 1
        return evaluator.evaluate(view)
    if material_key is None:
        parts = view.identity_sections()
        if parts is None:
            session.counts['actor_value_live_fallbacks'] += 1
            return evaluator.evaluate(view)
        try:
            material_key = parts.material_key()
        except (KeyError, ValueError):
            return evaluator.evaluate(view)
    key = material_key
    if view._prepared_score_key == key:
        session.counts['actor_value_hits'] += 1
        return view._prepared_score
    value = evaluator.evaluate(view)
    view._prepared_score_key, view._prepared_score = key, value
    session.counts['actor_value_renders'] += 1
    return value


class PureRatings(Sequence):
    """Detached immutable action/templates, with rows built only on consumption."""
    def __init__(self, actions, templates, counts):
        self.actions, self.templates, self.counts = actions, templates, counts
        self.rows = None

    def get(self):
        if self.rows is None:
            self.rows = tuple(sorted((ActionRating(action, *fields)
                for action, fields in zip(self.actions, self.templates)),
                key=lambda r: (-r.score, r.action.kind != 'PassPriorityAction', r.action.sort_key)))
            self.counts['actor_pure_ratings_materialized'] += len(self.rows)
        return self.rows

    def __len__(self):
        return len(self.actions)

    def __iter__(self):
        return iter(self.get())

    def __getitem__(self, key):
        return self.get()[key]

    def __eq__(self, other):
        try:
            other = tuple(other)
        except TypeError:
            return NotImplemented
        return self.get() == other


def pure_ratings(actions, session):
    if (not actions or not contract_matches(_Scorer.rate, _NATIVE_RATE_CONTRACT)
            or any(type(action) is not PlayerAction or action.kind not in _PURE_KINDS for action in actions)):
        return None
    templates = []
    for action in actions:
        template = session.pure_rating_templates.get(action.kind)
        if template is None:
            # Probe the actual native branch once per kind. An empty scorer
            # cannot consult a card map, remembered hand, evaluator or world.
            try:
                result = _Scorer.rate(object.__new__(_Scorer), action)
            except (AttributeError, KeyError, TypeError):
                return None
            if (result.action is not action or type(result.score) not in (float, int)
                    or type(result.supported) is not bool or type(result.reasons) is not tuple
                    or any(type(reason) is not str for reason in result.reasons)):
                return None
            template = result.score, result.supported, result.reasons
            session.pure_rating_templates[action.kind] = template
        templates.append(template)
    session.counts['actor_pure_rating_sets_deferred'] += 1
    return PureRatings(actions, tuple(templates), session.counts)
