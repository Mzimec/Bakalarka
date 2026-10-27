"""Replaceable action priors whose only input is acting-player information.

Action identities bind probabilities to the offered menu. They are not a neural
feature representation. Providers must be pure: no runtime State or simulator.
"""
from math import exp, fsum, isfinite
from typing import Mapping, Protocol

from game.ai.search_actions import PlayerAction
from game.ai.search_recall import PlayerInformation


class ActionPrior(Protocol):
    """!
    @brief Protocol mapping one player information set to nonnegative action weights.
    """
    def __call__(self, information: PlayerInformation) -> Mapping[PlayerAction, float]: ...


def normalize_prior(information, weights):
    """!
    @brief Validate a complete menu and normalize even very large finite weights.
    """
    if not isinstance(weights, Mapping) or set(weights) != set(information.actions):
        raise ValueError("Action prior must cover exactly the offered actions")
    values = {}
    for action in information.actions:
        value = weights[action]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError("Action prior weights must be finite and nonnegative")
        values[action] = float(value)
    scale = max(values.values(), default=0)
    if scale <= 0:
        raise ValueError("Action prior needs positive total weight")
    scaled = {a: v / scale for a, v in values.items()}
    total = fsum(scaled.values())
    return {a: v / total for a, v in scaled.items()}


class UniformActionPrior:
    """!
    @brief Assign equal prior weight to every offered complete action.
    """
    def __call__(self, information):
        return {a: 1.0 for a in information.actions}


class EncodedActionPrior:
    """!
    @brief Bind a model's menu-aligned logits to legal actions, without card IDs.

    The model receives (EncodedState, tuple[EncodedAction, ...]); no runtime or raw
    descriptions. Models own their weights/batching; this adapter validates outputs
    and supplies stable softmax. No trained network is bundled or implied.
    """
    def __init__(self, model, *, temperature=1.0, uniform_mix=0.05):
        if not callable(model):
            raise TypeError("Supply an encoded policy model")
        if not isfinite(temperature) or temperature <= 0:
            raise ValueError("temperature must be finite and positive")
        if not isfinite(uniform_mix) or not 0 <= uniform_mix <= 1:
            raise ValueError("uniform_mix must be in [0, 1]")
        self.model, self.temperature, self.uniform_mix = model, temperature, uniform_mix

    def __call__(self, information):
        from game.ai.search_encoding import ActionEncoder
        state, actions = ActionEncoder().encode_menu(information)
        logits = tuple(self.model(state, actions))
        if (len(logits) != len(actions) or not logits
                or any(type(v) not in (int, float) or not isfinite(v) for v in logits)):
            raise ValueError("Policy model must return one finite logit per offered action")
        maximum = max(logits)
        weights = {a: exp((v - maximum) / self.temperature) for a, v in zip(information.actions, logits)}
        probabilities = normalize_prior(information, weights)
        floor = self.uniform_mix / len(actions)
        return {a: (1 - self.uniform_mix) * p + floor for a, p in probabilities.items()}


class HeuristicActionPrior:
    """!
    @brief Stable softmax of local heuristic ratings, mixed with uniform support.

    Ratings are not calibrated win probabilities. Temperature and uniform mixture
    are explicit experimental parameters; this adapter does not remove candidates.
    """
    def __init__(self, policy=None, *, temperature=1.0, uniform_mix=0.05):
        from game.ai.observation_policy import ObservationOpponentPolicy
        if not isfinite(temperature) or temperature <= 0:
            raise ValueError("prior temperature must be finite and positive")
        if not isfinite(uniform_mix) or not 0 <= uniform_mix <= 1:
            raise ValueError("prior uniform_mix must be in [0, 1]")
        self.policy = policy if policy is not None else ObservationOpponentPolicy()
        self.temperature, self.uniform_mix = temperature, uniform_mix

    def __call__(self, information):
        ratings = self.policy.rank(information)
        if not ratings or any(not isfinite(r.score) for r in ratings):
            raise ValueError("Heuristic prior requires finite ratings")
        maximum = max(r.score for r in ratings)
        weights = {r.action: exp((r.score - maximum) / self.temperature) for r in ratings}
        probabilities = normalize_prior(information, weights)
        floor = self.uniform_mix / len(probabilities)
        return {a: (1 - self.uniform_mix) * p + floor for a, p in probabilities.items()}
