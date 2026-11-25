"""Schema-4 policy/value with differentiable AND/OR argument forests."""
from math import sqrt
import numpy as np

from game.ai.training.deck_model import DeckPolicyValueModel
from game.ai.training.tactical_features import TacticalFeatureEncoder, TacticalEntityState, TACTICAL_ARGUMENT_SIZE
from game.ai.evaluation.semantic_encoding import TacticalSemanticEncoder
from game.ai.evaluation.deck_profile import DeckProfiler
from game.ai.evaluation.action_tactics import compatible_opportunity, tactical_card_profile


class TacticalPolicyValueModel(DeckPolicyValueModel):
    model_version = feature_version = encoder_version = 4
    relation_size = TacticalEntityState.relation_width
    argument_size = TACTICAL_ARGUMENT_SIZE

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.semantic_encoder = TacticalSemanticEncoder()
        self.feature_encoder = TacticalFeatureEncoder()
        rng = np.random.default_rng(kwargs.get('seed',1) ^ 0x74524545)
        self.weights['wtree'] = rng.normal(0,1/sqrt(self.hidden),(self.hidden,self.hidden))
        self.first['wtree'] = np.zeros_like(self.weights['wtree'])
        self.second['wtree'] = np.zeros_like(self.weights['wtree'])

    def __setstate__(self, data):
        super().__setstate__(data)
        self.semantic_encoder = TacticalSemanticEncoder()
        self.feature_encoder = TacticalFeatureEncoder()

    def bind_decks(self, decks, catalog):
        profiler = DeckProfiler(self.semantic_encoder, matcher=compatible_opportunity, analyze_tree=tactical_card_profile)
        self.bind_profiles(tuple(profiler.profile(deck,catalog) for deck in decks))

    def _argument_hidden(self, inputs, items):
        parents, depths = [], []
        for state, actions in items:
            for action in actions:
                offset = len(parents)
                for local, parent in enumerate(action.argument_parents):
                    absolute = offset+parent if parent >= 0 else -1
                    parents.append(absolute)
                    depths.append(0 if absolute < 0 else depths[absolute]+1)
        parents, depths = np.asarray(parents,dtype=np.int64), np.asarray(depths,dtype=np.int64)
        base = inputs@self.weights['wargument']+self.weights['bargument']
        hidden, children = np.zeros_like(base), np.zeros_like(base)
        for depth in range(int(depths.max()) if len(depths) else -1,-1,-1):
            rows = np.flatnonzero(depths==depth)
            hidden[rows] = np.tanh(base[rows]+children[rows]@self.weights['wtree'])
            valid = rows[parents[rows]>=0]
            np.add.at(children,parents[valid],hidden[valid])
        return hidden, (parents, depths, children), parents < 0

    def _argument_backward(self, data, gradient):
        parents, depths, children = data['argument_aux']
        gradient, dz = gradient.copy(), np.zeros_like(gradient)
        for depth in range(int(depths.max())+1 if len(depths) else 0):
            rows = np.flatnonzero(depths==depth)
            valid = rows[parents[rows]>=0]
            gradient[valid] += dz[parents[valid]]@self.weights['wtree'].T
            dz[rows] = gradient[rows]*(1-data['hg'][rows]**2)
        return dz@self.weights['wargument'].T, {
            'wargument':data['Xg'].T@dz, 'bargument':dz.sum(axis=0), 'wtree':children.T@dz}
