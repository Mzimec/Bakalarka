"""CPU Deep Sets policy/value model with explicit entity/argument relations.

Learned semantic and entity transforms precede pooling. Relation and action
argument transforms see the actual endpoint embeddings. NumPy gradients are
explicit and tested numerically; no new mandatory ML framework is required.
"""
from math import sqrt
from time import perf_counter
import numpy as np

from game.ai.training.training_model import PolicyValueModel
from game.ai.training.entity_features import (EntityFeatureEncoder, EntityState, SEMANTIC_SIZE,
    ENTITY_SIZE, GLOBAL_SIZE, RELATION_SIZE, ARGUMENT_SIZE, ACTION_SIZE, GROUPS)
from game.ai.evaluation.evaluation_cache import EvaluationCache
from game.ai.evaluation.semantic_encoding import CardSemanticEncoder


class DeepSetsPolicyValueModel(PolicyValueModel):
    """Version-2 entity model, compatible with the shared prior/value adapters."""
    model_version = feature_version = encoder_version = 2
    semantic_size = SEMANTIC_SIZE
    entity_size = ENTITY_SIZE
    global_size = GLOBAL_SIZE
    relation_size = RELATION_SIZE
    argument_size = ARGUMENT_SIZE
    action_size = ACTION_SIZE
    groups = GROUPS

    def __init__(self, *, hidden=32, seed=1, cache_size=256, cache_bytes=32*1024*1024):
        if type(hidden) is not int or hidden < 1:
            raise ValueError('hidden must be a positive integer')
        self.hidden, self.step = hidden, 0
        self.training_games_seen = 0
        rng = np.random.default_rng(seed)
        dimensions = dict(sem=self.semantic_size, entity=self.entity_size+hidden,
                          relation=self.relation_size+2*hidden, argument=self.argument_size+3*hidden,
                          s=self.global_size+self.groups*(hidden+1)+hidden+1, a=self.action_size+3*hidden)
        self.weights = {}
        for name, width in dimensions.items():
            self.weights['w'+name] = rng.normal(0,1/sqrt(width),(width,hidden))
            self.weights['b'+name] = np.zeros(hidden)
        self.weights.update(wp=rng.normal(0,1/sqrt(hidden),hidden),
                            wv=rng.normal(0,1/sqrt(hidden),hidden), bv=np.zeros(1))
        self.first = {k:np.zeros_like(v) for k,v in self.weights.items()}
        self.second = {k:np.zeros_like(v) for k,v in self.weights.items()}
        self.cache = EvaluationCache(cache_size,cache_bytes)
        self.semantic_encoder = CardSemanticEncoder()
        self.feature_encoder = EntityFeatureEncoder()
        self.semantic_matrices = EvaluationCache(256,32*1024*1024)

    def __getstate__(self):
        data = super().__getstate__()
        data.pop('semantic_encoder',None)
        data.pop('feature_encoder',None)
        data.pop('semantic_matrices',None)
        return data

    def __setstate__(self, data):
        super().__setstate__(data)
        self.semantic_encoder = CardSemanticEncoder()
        self.feature_encoder = EntityFeatureEncoder()
        self.semantic_matrices = EvaluationCache(256,32*1024*1024)

    def _semantic_matrix(self, rows):
        # Strongly retain the immutable facts with the array: object IDs cannot
        # be recycled while cached. These are inputs, never learned embeddings.
        key = ('semantic_matrix',id(rows))
        found = self.semantic_matrices.get(key)
        if found is not None and found[0] is rows:
            return found[1]
        array = np.asarray(rows,dtype=np.float64).reshape((-1,self.semantic_size))
        array.setflags(write=False)
        self.semantic_matrices.put(key,(rows,array),len(rows)*self.semantic_size)
        return array

    def _state_key(self, information):
        # Unlike pooled v1, schema 2 explicitly encodes the request kind.
        return (*super()._state_key(information),information.current.request_kind)

    def encode_information(self, information):
        state_key = self._state_key(information)
        menu_key = ('menu',state_key,tuple((a.kind,a.structured.value.digest) for a in information.actions))
        found = self.cache.get(menu_key)
        if found is not None:
            return found
        started = perf_counter()
        features = self.feature_encoder.encode(information)
        self.cache.counts['encoding_ms'] += (perf_counter()-started)*1000
        state, actions = features
        size = (len(state.globals)+len(state.entities)*self.entity_size
                +sum(len(r) for r in state.semantics)*self.semantic_size
                +len(state.relations)*(self.relation_size+2)
                +sum(self.action_size+len(a.semantics)*self.semantic_size
                     +sum(self.argument_size+len(arg.semantics)*self.semantic_size+2 for arg in a.arguments) for a in actions))
        return self.cache.put(menu_key,features,size)

    def evaluate_information(self, information):
        key = ('value',self._state_key(information))
        found = self.cache.get(key)
        if found is not None:
            return found
        state, _ = self.encode_information(information)
        started = perf_counter()
        value = self.value(state)
        self.cache.counts['inference_ms'] += (perf_counter()-started)*1000
        return self.cache.put(key,value,1)

    def _forward(self, items):
        if not items:
            raise ValueError('Empty inference batch')
        H, w = self.hidden, self.weights
        erows, ebundles, egroups = [], [], []
        rrows, rsource, rtarget, rowners = [], [], [], []
        arows, asource, abundles, aowners = [], [], [], []
        grows, gsource, gtarget, gbundles, gowners = [], [], [], [], []
        semmatrices, semowners, bundle_count, counts = [], [], 0, []
        def bundle(rows):
            nonlocal bundle_count
            index = bundle_count
            bundle_count += 1
            semmatrices.append(self._semantic_matrix(rows))
            semowners.extend([index]*len(rows))
            return index
        for sample, (state, actions) in enumerate(items):
            if not isinstance(state, EntityState) or state.feature_version != self.feature_version:
                raise ValueError('Deep Sets requires entity feature schema 2')
            state.validate()
            offset = len(erows)
            def reference(index): return offset+index if index >= 0 else -1
            counts.append(len(actions))
            for row, semantics, group in zip(state.entities,state.semantics,state.groups):
                erows.append(row)
                ebundles.append(bundle(semantics))
                egroups.append(sample*self.groups+group)
            for source,target,row in state.relations:
                rsource.append(reference(source)); rtarget.append(reference(target))
                rrows.append(row); rowners.append(sample)
            for action in actions:
                if action.argument_width != self.argument_size or action.action_width != self.action_size:
                    raise ValueError('Action feature schema does not match model')
                action.validate(state)
                index = len(arows)
                arows.append(action.features); asource.append(reference(action.source))
                abundles.append(bundle(action.semantics)); aowners.append(sample)
                for argument in action.arguments:
                    grows.append(argument.features); gsource.append(reference(argument.source))
                    gtarget.append(reference(argument.target)); gowners.append(index)
                    gbundles.append(bundle(argument.semantics))
        def matrix(rows,width): return np.asarray(rows,dtype=np.float64).reshape((-1,width))
        def indexes(rows): return np.asarray(rows,dtype=np.int64)
        Xsem = np.concatenate(semmatrices,axis=0) if semmatrices else np.empty((0,self.semantic_size))
        semowners, ebundles, egroups = map(indexes,(semowners,ebundles,egroups))
        rsource,rtarget,rowners = map(indexes,(rsource,rtarget,rowners))
        asource,abundles,aowners = map(indexes,(asource,abundles,aowners))
        gsource,gtarget,gbundles,gowners = map(indexes,(gsource,gtarget,gbundles,gowners))
        hsem = np.tanh(Xsem@w['wsem']+w['bsem'])
        pooled_sem = np.zeros((bundle_count,H))
        np.add.at(pooled_sem,semowners,hsem)
        Xe = np.concatenate((matrix(erows,self.entity_size),pooled_sem[ebundles]),axis=1)
        he = np.tanh(Xe@w['wentity']+w['bentity'])
        endpoints = np.vstack((he,np.zeros((1,H))))  # -1 is the absent-reference row
        Xr = np.concatenate((matrix(rrows,self.relation_size),endpoints[rsource],endpoints[rtarget]),axis=1)
        hr = np.tanh(Xr@w['wrelation']+w['brelation'])
        relation_pool = np.zeros((len(items),H))
        np.add.at(relation_pool,rowners,hr)
        groups = np.zeros((len(items)*self.groups,H))
        np.add.at(groups,egroups,he)
        group_counts = np.bincount(egroups,minlength=len(items)*self.groups).reshape((len(items),self.groups))
        relation_counts = np.bincount(rowners,minlength=len(items))
        Xs = np.concatenate((np.asarray([s.globals for s,a in items]),groups.reshape((len(items),-1)),
                             np.log1p(group_counts),relation_pool,np.log1p(relation_counts)[:,None]),axis=1)
        hs = np.tanh(Xs@w['ws']+w['bs'])
        Xg = np.concatenate((matrix(grows,self.argument_size),endpoints[gsource],endpoints[gtarget],
                             pooled_sem[gbundles]),axis=1)
        hg, argument_aux, argument_roots = self._argument_hidden(Xg, items)
        arguments = np.zeros((len(arows),H))
        np.add.at(arguments,gowners[argument_roots],hg[argument_roots])
        Xa = np.concatenate((matrix(arows,self.action_size),endpoints[asource],pooled_sem[abundles],arguments),axis=1)
        ha = np.tanh(Xa@w['wa']+w['ba'])
        logits = ha@w['wp']+np.sum(ha*hs[aowners],axis=1)/sqrt(H)
        values = np.tanh(hs@w['wv']+w['bv'][0])
        return locals()

    def _argument_hidden(self, inputs, items):
        """Flat schema-2/3 arguments; later schemas may preserve a forest."""
        hidden = np.tanh(inputs@self.weights['wargument']+self.weights['bargument'])
        return hidden, None, np.ones(len(inputs), dtype=bool)

    def _argument_backward(self, data, gradient):
        dz = gradient*(1-data['hg']**2)
        return dz@self.weights['wargument'].T, {
            'wargument':data['Xg'].T@dz, 'bargument':dz.sum(axis=0)}

    def predict_batch(self, items):
        data = self._forward(items)
        result, offset = [], 0
        for count,value in zip(data['counts'],data['values']):
            result.append((data['logits'][offset:offset+count].tolist(),float(value)))
            offset += count
        return result

    def value(self, state):
        return float(self._forward([(state,())])['values'][0])

    def loss_gradients(self, batch, *, weight_decay=.0001, value_weight=1.):
        if not batch or any(not np.isfinite(v) or v < 0 for v in (weight_decay,value_weight)):
            raise ValueError('Invalid training batch/loss weights')
        d = self._forward([(r.state,r.actions) for r in batch])
        H, w, n = self.hidden, self.weights, len(batch)
        targets = np.asarray([r.outcome for r in batch])
        if not np.isfinite(targets).all() or np.any(np.abs(targets)>1):
            raise ValueError('Invalid terminal targets')
        delta, ce, offset = np.zeros_like(d['logits']),0.,0
        for row,count in zip(batch,d['counts']):
            target = np.asarray(row.policy)
            if (not count or target.shape != (count,) or not np.isfinite(target).all()
                    or np.any(target<0) or not np.isclose(target.sum(),1)):
                raise ValueError('Invalid menu policy target')
            scores = d['logits'][offset:offset+count]
            stable = scores-scores.max()
            logp = stable-np.log(np.exp(stable).sum())
            ce -= float(target@logp)/n
            delta[offset:offset+count] = (np.exp(logp)-target)/n
            offset += count
        hs, ha, values = d['hs'],d['ha'],d['values']
        dv = 2*value_weight*(values-targets)*(1-values**2)/n
        dhs = dv[:,None]*w['wv'][None,:]
        np.add.at(dhs,d['aowners'],delta[:,None]*ha/sqrt(H))
        dha = delta[:,None]*(w['wp'][None,:]+hs[d['aowners']]/sqrt(H))
        grads = dict(wp=ha.T@delta,wv=hs.T@dv,bv=np.array([dv.sum()]))
        def layer(name,inputs,hidden,gradient):
            dz = gradient*(1-hidden**2)
            grads['w'+name] = inputs.T@dz
            grads['b'+name] = dz.sum(axis=0)
            return dz@w['w'+name].T
        dXa = layer('a',d['Xa'],ha,dha)
        dXs = layer('s',d['Xs'],hs,dhs)
        dhe = np.zeros_like(d['he'])
        dp = np.zeros_like(d['pooled_sem'])
        def endpoint(indexes,gradient):
            valid = indexes >= 0
            np.add.at(dhe,indexes[valid],gradient[valid])
        endpoint(d['asource'],dXa[:,self.action_size:self.action_size+H])
        np.add.at(dp,d['abundles'],dXa[:,self.action_size+H:self.action_size+2*H])
        dg = np.zeros_like(d['hg'])
        roots = d['argument_roots']
        dg[roots] = dXa[:,self.action_size+2*H:][d['gowners'][roots]]
        dXg, argument_grads = self._argument_backward(d,dg)
        grads.update(argument_grads)
        endpoint(d['gsource'],dXg[:,self.argument_size:self.argument_size+H])
        endpoint(d['gtarget'],dXg[:,self.argument_size+H:self.argument_size+2*H])
        np.add.at(dp,d['gbundles'],dXg[:,self.argument_size+2*H:])
        group_grad = dXs[:,self.global_size:self.global_size+self.groups*H].reshape((-1,H))
        dhe += group_grad[d['egroups']]
        relation_offset = self.global_size+self.groups*H+self.groups
        dr = dXs[:,relation_offset:relation_offset+H][d['rowners']]
        dXr = layer('relation',d['Xr'],d['hr'],dr)
        endpoint(d['rsource'],dXr[:,self.relation_size:self.relation_size+H])
        endpoint(d['rtarget'],dXr[:,self.relation_size+H:])
        dXe = layer('entity',d['Xe'],d['he'],dhe)
        np.add.at(dp,d['ebundles'],dXe[:,self.entity_size:])
        layer('sem',d['Xsem'],d['hsem'],dp[d['semowners']])
        regularization = 0.
        for key,value in w.items():
            if key.startswith('w'):
                grads[key] += weight_decay*value
                regularization += weight_decay*float(np.sum(value**2))/2
        mse = float(np.mean((values-targets)**2))
        return dict(policy_loss=ce,value_loss=mse,regularization=regularization,
                    loss=ce+value_weight*mse+regularization),grads
