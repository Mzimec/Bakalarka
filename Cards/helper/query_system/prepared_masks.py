"""Loan-owned native query admission and revision-certified index groups.

This is an explicit internal contract, not a replacement for extensible Query:
the owner admits native code for the whole loan, frozen query fields stay frozen,
and committed index writes advance membership tokens. Raw restoration calls
invalidate(). No mask/result is cached; every evaluation reads current bits.
"""
from .native_masks import _MaskContext, _Unsupported
from .query import EqQuery, InQuery, RangeQuery, HasQuery, AndQuery, OrQuery, DifferenceQuery
from .object_register import ObjectStorage, SimpleObjectIndexProvider


class PreparedQuerySession:
    """One last-query plan and register certificates for one native loan."""
    def __init__(self, evaluator, counts, *, allow_unversioned=False):
        self.evaluator, self.counts = evaluator, counts
        self.allow_unversioned = allow_unversioned
        self.last_query,self.last_accepted = None,False
        self.indexes,self.unversioned = {},{}
        self.refresh()

    def refresh(self):
        self.close()
        evaluator = self.evaluator
        # The native API/field contract is checked here, never per evaluation.
        self.enabled = (evaluator.intact()
            and ObjectStorage.__getattribute__ is object.__getattribute__
            and SimpleObjectIndexProvider.__getattribute__ is object.__getattribute__ and all(
            cls.__getattribute__ is object.__getattribute__ and not any(
                hasattr(type(vars(parent).get(name)), '__get__')
                for parent in cls.__mro__ for name in fields)
            for cls,fields in evaluator.fields.items()))
        self.counts['plan_contract_checks'] += 1

    def admit(self, query, depth=0):
        if depth == 0 and self.last_query is query:
            self.counts['plan_reuses'] += 1
            return self.last_accepted
        cls = type(query)
        if cls not in self.evaluator.fields or depth > 64:
            return False
        data = vars(query)
        fields = self.evaluator.fields[cls]
        if ('eval' in data or 'estimate' in data or any(name not in data for name in fields)):
            return False
        evaluator = self.evaluator
        if cls is EqQuery:
            accepted = evaluator.index_key(data['idx_key']) and evaluator.pure_key(data['key'])
        elif cls is InQuery:
            accepted = (evaluator.index_key(data['idx_key']) and type(data['keys']) is frozenset
                and all(evaluator.pure_key(key) for key in data['keys']))
        elif cls is RangeQuery:
            accepted = (evaluator.index_key(data['idx_key'])
                and type(data['min_value']) in evaluator.scalar and type(data['max_value']) in evaluator.scalar)
        elif cls is HasQuery:
            accepted = evaluator.index_key(data['idx_key'])
        else:
            children = data['children'] if cls in (AndQuery,OrQuery) else data['excludes']
            accepted = (type(children) is tuple and
                (cls is not DifferenceQuery or self.admit(data['base'],depth+1))
                and all(self.admit(child,depth+1) for child in children))
        # Frozen query itself is the executable plan; no copied tree/closures.
        # Most engine queries are single-use. Retain only the last root, with
        # an identity witness rather than hashing or retaining a query forest.
        if depth == 0:
            self.last_query,self.last_accepted = query,accepted
            self.counts['plan_admissions'] += 1
        return accepted

    def evaluate(self, register, query):
        if not self.enabled or not self.admit(query):
            return None
        data = vars(register)
        storage,provider,tokens = data.get('_storage'),data.get('_idx_provider'),data.get('_membership_tokens')
        entry = self.indexes.get(id(register))
        if entry is None or entry[0] is not register or not entry[1].matches(storage,provider,tokens):
            if (type(storage) is not ObjectStorage or type(provider) is not SimpleObjectIndexProvider
                    or not (tokens is None and self.allow_unversioned or type(tokens) is dict
                        and all(self.evaluator.index_key(key) for key in tokens))
                    or type(vars(storage).get('_id_to_obj')) is not list
                    or type(vars(storage).get('_free_ids')) is not list):
                return None
            values = vars(provider)
            if ('get_index_group' in values or 'get_ordered_group' in values
                    or type(values.get('_data')) is not dict or type(values.get('_ordered_keys')) is not set
                    or not all(self.evaluator.index_key(key) for key in values['_data'])
                    or not all(self.evaluator.index_key(key) for key in values['_ordered_keys'])):
                return None
            index = _PreparedIndex(self.evaluator,storage,provider,tokens,self.counts)
            self.indexes[id(register)] = register,index
            if tokens is None:
                entry = self.unversioned.setdefault(id(provider),(provider,set()))
                entry[1].add(id(register))
        else:
            index = entry[1]
        try:
            return index.mask(query)
        except _Unsupported:
            return None

    def invalidate(self):
        """Raw checkpoint restoration bypasses normal committed index writers."""
        self.indexes.clear()
        self.unversioned.clear()
        self.counts['plan_raw_invalidations'] += 1

    def writing(self,provider):
        """Unversioned native providers are fenced by their owner's API."""
        entry = self.unversioned.pop(id(provider),None)
        if entry is not None and entry[0] is provider:
            for identity in entry[1]:
                saved = self.indexes.get(identity)
                if saved is not None and saved[1].provider is provider:
                    del self.indexes[identity]
                    self.counts['plan_provider_invalidations'] += 1

    def close(self):
        self.indexes.clear()
        self.unversioned.clear()
        self.last_query,self.last_accepted = None,False


class _PreparedIndex(_MaskContext):
    __slots__ = ('tokens','certificates','counts','data','ordered')

    def __init__(self,evaluator,storage,provider,tokens,counts):
        super().__init__(evaluator,storage,provider)
        self.tokens,self.counts = tokens,counts
        self.certificates = {}
        self.data,self.ordered = provider._data,provider._ordered_keys

    def matches(self,storage,provider,tokens):
        return (self.storage is storage and self.provider is provider and self.tokens is tokens
            and self.data is provider._data and self.ordered is provider._ordered_keys)

    def group(self,key):
        # Group identity covers binding replacement. Opaque token identity
        # covers content changes, including rollback then a different sibling.
        values = self.provider.get_index_group(key)
        token = self if self.tokens is None else self.tokens.get(key)
        if token is None:
            raise _Unsupported
        certificate = self.certificates.get(key)
        if certificate is not None and certificate[0] is token and certificate[1] is values:
            self.counts['plan_group_reuses'] += 1
            if not certificate[2]:
                raise _Unsupported
            return values
        self.groups.pop(key,None)
        self.counts['plan_group_checks'] += 1
        try:
            result = super().group(key)
        except _Unsupported:
            self.certificates[key] = token,values,False
            raise
        self.certificates[key] = token,values,True
        return result
