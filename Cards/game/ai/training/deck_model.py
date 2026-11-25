"""Version 3 deck-conditioned Deep Sets, sharing the tested ragged learner."""
from game.ai.training.deep_sets_model import DeepSetsPolicyValueModel
from game.ai.training.deck_features import DeckFeatureEncoder, DeckEntityState
from game.ai.evaluation.deck_profile import DeckProfiler
from game.ai.evaluation.semantic_encoding import DeckSemanticEncoder


class DeckPolicyValueModel(DeepSetsPolicyValueModel):
    model_version = feature_version = encoder_version = 3
    entity_size = DeckEntityState.entity_width
    global_size = DeckEntityState.global_width
    relation_size = DeckEntityState.relation_width

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.semantic_encoder = DeckSemanticEncoder()
        self.feature_encoder = DeckFeatureEncoder()

    def __setstate__(self, data):
        super().__setstate__(data)
        self.semantic_encoder = DeckSemanticEncoder()
        self.feature_encoder = DeckFeatureEncoder()

    def bind_decks(self, decks, catalog):
        profiler = DeckProfiler(self.semantic_encoder)
        self.bind_profiles(tuple(profiler.profile(d, catalog) for d in decks))

    def bind_profiles(self, profiles):
        self.feature_encoder.bind_profiles(profiles)
        self.configure_cache(self.cache.entries, self.cache.byte_limit)
        from game.ai.evaluation.evaluation_cache import EvaluationCache
        self.semantic_matrices = EvaluationCache(self.semantic_matrices.entries,self.semantic_matrices.byte_limit)

    def _state_key(self, information):
        return (*super()._state_key(information), self.feature_encoder.context_key)
