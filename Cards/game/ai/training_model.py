"""Small CPU policy/value network; optional NumPy dependency, ragged legal menus.

Two tanh encoders score (state, action) pairs. This is a reproducible baseline,
not a claim that pooled encoder v1 is a complete rules/belief representation.
"""
from hashlib import sha256
from io import BytesIO
import json
from math import sqrt, isfinite
from time import perf_counter

import numpy as np

from game.ai.training_features import STATE_SIZE, ACTION_SIZE, FEATURE_VERSION, state_vector
from game.ai.search_encoding import SCHEMA_VERSION, StateEncoder
from game.ai.evaluation_cache import EvaluationCache

MODEL_VERSION = 1


class PolicyValueModel:
    """!
    @brief Train and evaluate the CPU policy/value model over numeric features.
    """
    def __init__(self, *, hidden=32, seed=1, cache_size=256, cache_bytes=32*1024*1024):
        if type(hidden) is not int or hidden < 1:
            raise ValueError('hidden must be a positive integer')
        rng = np.random.default_rng(seed)
        self.hidden, self.step = hidden, 0
        self.weights = dict(ws=rng.normal(0,1/sqrt(STATE_SIZE),(STATE_SIZE,hidden)),
            bs=np.zeros(hidden), wa=rng.normal(0,1/sqrt(ACTION_SIZE),(ACTION_SIZE,hidden)),
            ba=np.zeros(hidden), wp=rng.normal(0,1/sqrt(hidden),hidden),
            wv=rng.normal(0,1/sqrt(hidden),hidden), bv=np.zeros(1))
        self.first = {k:np.zeros_like(v) for k,v in self.weights.items()}
        self.second = {k:np.zeros_like(v) for k,v in self.weights.items()}
        self.cache = EvaluationCache(cache_size,cache_bytes)

    def __getstate__(self):
        # Do not multiply warmed caches in parallel worker payloads.
        return {k:v for k,v in vars(self).items() if k!='cache'} | {
            '_cache_limits':(self.cache.entries,self.cache.byte_limit)}

    def __setstate__(self, data):
        data = dict(data)
        limits = data.pop('_cache_limits',(256,32*1024*1024))
        self.__dict__.update(data)
        self.cache = EvaluationCache(*limits)

    def configure_cache(self, entries, byte_limit):
        """!
        @brief Configure bounded feature and output caching for this model.
        """
        self.cache = EvaluationCache(entries,byte_limit)

    def invalidate_cache(self):
        """!
        @brief Also call after externally editing weights in place.
        """
        self.cache.clear_outputs()

    def _state_key(self, information):
        # Encoder v1 reads only this detached view, not recall/request/menu.
        # This key is NEVER used as search identity or world equivalence.
        return (SCHEMA_VERSION,FEATURE_VERSION,information.current.player,
                information.current.structured.value.digest)

    def encode_information(self, information):
        """!
        @brief Encode player information under the model feature-schema contract.
        """
        from game.ai.training_features import encode_training
        state_key = self._state_key(information)
        menu_key = ('menu',state_key,tuple((a.kind,a.structured.value.digest) for a in information.actions))
        found = self.cache.get(menu_key)
        if found is not None:
            return found
        started = perf_counter()
        features = encode_training(information)
        self.cache.counts['encoding_ms'] += (perf_counter()-started)*1000
        self.cache.put(('state',state_key),features[0],STATE_SIZE)
        return self.cache.put(menu_key,features,STATE_SIZE+len(features[1])*ACTION_SIZE)

    def predict_information(self, information):
        """!
        @brief Predict offered-action probabilities and value from actor information.
        """
        state_key = self._state_key(information)
        key = ('prediction',state_key,tuple((a.kind,a.structured.value.digest) for a in information.actions))
        found = self.cache.get(key)
        if found is not None:
            return found
        features = self.encode_information(information)
        started = perf_counter()
        logits,value = self.predict_batch([features])[0]
        self.cache.counts['inference_ms'] += (perf_counter()-started)*1000
        return self.cache.put(key,(tuple(logits),value),len(logits)+1)

    def evaluate_information(self, information):
        """!
        @brief Evaluate actor facts with revision-scoped feature and output reuse.
        """
        key = self._state_key(information)
        value = self.cache.get(('value',key))
        if value is not None:
            return value
        features = self.cache.get(('state',key))
        if features is None:
            started = perf_counter()
            features = state_vector(StateEncoder().encode(information))
            self.cache.counts['encoding_ms'] += (perf_counter()-started)*1000
            self.cache.put(('state',key),features,STATE_SIZE)
        started = perf_counter()
        value = self.value(features)
        self.cache.counts['inference_ms'] += (perf_counter()-started)*1000
        return self.cache.put(('value',key),value,1)

    @property
    def revision(self):
        """!
        @brief Identify the weights revision scoping cached model evaluations.
        """
        digest = sha256()
        digest.update(str((MODEL_VERSION, FEATURE_VERSION, self.hidden, self.step)).encode())
        for key,value in sorted(self.weights.items()):
            digest.update(key.encode())
            digest.update(value.tobytes())
        return digest.hexdigest()

    def _forward(self, items):
        if not items:
            raise ValueError('Empty inference batch')
        states = np.asarray([s for s,a in items], dtype=np.float64)
        actions = np.asarray([row for s,a in items for row in a], dtype=np.float64)
        counts = [len(a) for s,a in items]
        if states.shape != (len(items),STATE_SIZE) or not all(counts) or actions.shape != (sum(counts),ACTION_SIZE):
            raise ValueError('Incompatible feature dimensions or empty menu')
        if not np.isfinite(states).all() or not np.isfinite(actions).all():
            raise ValueError('Nonfinite model input')
        owners = np.repeat(np.arange(len(items)), counts)
        w = self.weights
        hs, ha = np.tanh(states@w['ws']+w['bs']), np.tanh(actions@w['wa']+w['ba'])
        logits = ha@w['wp'] + np.sum(ha*hs[owners], axis=1)/sqrt(self.hidden)
        values = np.tanh(hs@w['wv']+w['bv'][0])
        return states, actions, counts, owners, hs, ha, logits, values

    def predict_batch(self, items):
        """!
        @brief Evaluate a batch of detached numeric policy/value inputs.
        """
        *_, counts, owners, hs, ha, logits, values = self._forward(items)
        result, offset = [], 0
        for count,value in zip(counts, values):
            result.append((logits[offset:offset+count].tolist(), float(value)))
            offset += count
        return result

    def value(self, features):
        """!
        @brief Compute the configured value for the supplied visible position or card.
        """
        state = np.asarray(features, dtype=np.float64)
        if state.shape != (STATE_SIZE,) or not np.isfinite(state).all():
            raise ValueError('Incompatible value input')
        w = self.weights
        return float(np.tanh(np.tanh(state@w['ws']+w['bs'])@w['wv']+w['bv'][0]))

    def loss_gradients(self, batch, *, weight_decay=0.0001, value_weight=1.0):
        """!
        @brief Compute policy/value loss and gradients for the supplied numeric batch.
        """
        if not batch or any(not isfinite(x) or x < 0 for x in (weight_decay,value_weight)):
            raise ValueError('Invalid training batch/loss weights')
        states, actions, counts, owners, hs, ha, logits, values = self._forward(
            [(row.state, row.actions) for row in batch])
        target_values = np.asarray([row.outcome for row in batch], dtype=np.float64)
        if not np.isfinite(target_values).all() or np.any(np.abs(target_values)>1):
            raise ValueError('Invalid terminal targets')
        delta, ce, offset = np.zeros_like(logits), 0., 0
        for count,row in zip(counts, batch):
            target = np.asarray(row.policy, dtype=np.float64)
            if target.shape != (count,) or not np.isfinite(target).all() or np.any(target<0) or not np.isclose(target.sum(),1):
                raise ValueError('Invalid menu policy target')
            scores = logits[offset:offset+count]
            stable = scores-scores.max()
            logp = stable-np.log(np.exp(stable).sum())
            ce -= float(target@logp)/len(batch)
            delta[offset:offset+count] = (np.exp(logp)-target)/len(batch)
            offset += count
        w = self.weights
        dv = 2*value_weight*(values-target_values)*(1-values**2)/len(batch)
        ds = dv[:,None]*w['wv'][None,:]
        np.add.at(ds, owners, delta[:,None]*ha/sqrt(self.hidden))
        da = delta[:,None]*(w['wp'][None,:]+hs[owners]/sqrt(self.hidden))*(1-ha**2)
        ds *= 1-hs**2
        grads = dict(ws=states.T@ds, bs=ds.sum(axis=0), wa=actions.T@da,
            ba=da.sum(axis=0), wp=ha.T@delta, wv=hs.T@dv, bv=np.array([dv.sum()]))
        reg = 0.
        for key in ('ws','wa','wp','wv'):
            grads[key] += weight_decay*w[key]
            reg += weight_decay*float(np.sum(w[key]**2))/2
        mse = float(np.mean((values-target_values)**2))
        return dict(policy_loss=ce, value_loss=mse, regularization=reg,
                    loss=ce+value_weight*mse+reg), grads

    def train_batch(self, batch, *, learning_rate=.001, weight_decay=.0001, value_weight=1., clip_norm=5.):
        """!
        @brief Apply one learner update and advance the model revision.
        """
        if any(not isfinite(v) or v <= 0 for v in (learning_rate,clip_norm)):
            raise ValueError('Invalid optimizer settings')
        losses, grads = self.loss_gradients(batch, weight_decay=weight_decay, value_weight=value_weight)
        norm = sqrt(sum(float(np.sum(g*g)) for g in grads.values()))
        if not isfinite(norm):
            raise ValueError('Nonfinite gradient')
        scale = min(1., clip_norm/max(norm,1e-30))
        self.step += 1
        for key,grad in grads.items():
            grad = grad*scale
            self.first[key] = .9*self.first[key]+.1*grad
            self.second[key] = .999*self.second[key]+.001*grad**2
            m = self.first[key]/(1-.9**self.step)
            v = self.second[key]/(1-.999**self.step)
            self.weights[key] -= learning_rate*m/(np.sqrt(v)+1e-8)
        self.invalidate_cache()
        return losses | dict(gradient_norm=norm, step=self.step)

    def to_bytes(self):
        """!
        @brief Immutable, versioned snapshot for IPC; never opens a disk file.
        """
        metadata = dict(model_version=MODEL_VERSION, feature_version=FEATURE_VERSION,
            encoder_version=SCHEMA_VERSION, hidden=self.hidden, step=self.step, revision=self.revision)
        arrays = {prefix+k:v for prefix,rows in (('w_',self.weights),('m_',self.first),('v_',self.second)) for k,v in rows.items()}
        with BytesIO() as stream:
            np.savez(stream, metadata=np.array(json.dumps(metadata)), **arrays)
            return stream.getvalue()

    @classmethod
    def from_bytes(cls, snapshot):
        """!
        @brief Validate an in-memory model snapshot without filesystem access.
        """
        if not isinstance(snapshot, bytes):
            raise TypeError('Model snapshot must be immutable bytes')
        return cls.load(BytesIO(snapshot))

    @classmethod
    def load(cls, path):
        """!
        @brief Read an existing checkpoint or memory stream; no checkpoint writes.
        """
        with np.load(path, allow_pickle=False) as archive:
            metadata = json.loads(str(archive['metadata']))
            if (metadata['model_version'],metadata['feature_version'],metadata['encoder_version']) != (MODEL_VERSION,FEATURE_VERSION,SCHEMA_VERSION):
                raise ValueError('Incompatible checkpoint schema')
            model = cls(hidden=metadata['hidden'])
            model.step = metadata['step']
            if type(model.step) is not int or model.step < 0:
                raise ValueError('Invalid optimizer step')
            for prefix,rows in (('w_',model.weights),('m_',model.first),('v_',model.second)):
                for key,previous in rows.items():
                    value = archive[prefix+key]
                    if value.shape != previous.shape or not np.isfinite(value).all():
                        raise ValueError('Invalid checkpoint parameters')
                    rows[key] = np.asarray(value, dtype=np.float64).copy()
        if model.revision != metadata['revision']:
            raise ValueError('Checkpoint revision mismatch')
        return model


class NeuralPrior:
    """!
    @brief Bind revision-scoped model outputs to exactly the offered action menu.
    """
    def __init__(self, model):
        self.model = model

    def __call__(self, information):
        from game.ai.action_priors import normalize_prior
        logits,_ = self.model.predict_information(information)
        maximum = max(logits)
        return normalize_prior(information, {a:float(np.exp(v-maximum)) for a,v in zip(information.actions,logits)})


class NeuralValue:
    """!
    @brief Evaluate a player-visible leaf using the configured policy/value model.
    """
    def __init__(self, model):
        self.model = model

    def evaluate(self, view):
        """!
        @brief Score the supplied player-visible position from its observer perspective.
        """
        from game.ai.structured_values import StructuredJSON
        return self.evaluate_structured(StructuredJSON(view),view['self'])

    def evaluate_structured(self, view, player):
        """!
        @brief Evaluate an immutable viewer-filtered payload under the model contract.
        """
        from game.ai.search_observation import ActingPlayerObservation
        from game.ai.search_recall import PlayerInformation, PlayerRecall
        info = PlayerInformation(ActingPlayerObservation(player, 'PriorityDecisionRequest',view), PlayerRecall(), ())
        return self.model.evaluate_information(info)
