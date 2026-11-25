"""Pure native query evaluation as integer masks, with whole-query fallback.

The public Query.eval/BitSet protocol is unchanged. This kernel accepts only
unchanged native methods, plain index data and side-effect-free query keys.
Unsupported inputs return None before any extension callback is evaluated.
"""
from enum import Enum
from bisect import bisect_left, bisect_right
from types import FunctionType
from inspect import getattr_static

from . import query as queries, object_register as registers
from .query import EqQuery, InQuery, RangeQuery, HasQuery, AndQuery, OrQuery, DifferenceQuery
from .object_register import BitSet, IndexKey, ObjectStorage, SimpleObjectIndexProvider, SortedOrderedMap


class _Unsupported(Exception):
    pass


def _plain_setter(cls):
    function = cls.__setattr__
    seen = set()
    while hasattr(function,'__runtime_rule_passthrough__') and id(function) not in seen:
        seen.add(id(function))
        function = function.__runtime_rule_passthrough__
    return function is object.__setattr__


def _origin(function, suffix, name):
    seen = set()
    while hasattr(function,'__runtime_rule_passthrough__') and id(function) not in seen:
        seen.add(id(function))
        function = function.__runtime_rule_passthrough__
    if (not isinstance(function,FunctionType) or function.__qualname__ != name
            or not function.__code__.co_filename.replace('\\','/').endswith(suffix)):
        return None
    return function, function.__code__


class NativeMaskEvaluator:
    """A scope-local native contract; no result or runtime cache survives calls."""
    types = (EqQuery,InQuery,RangeQuery,HasQuery,AndQuery,OrQuery,DifferenceQuery)
    scalar = (str,int,float,bool,bytes,type(None))

    def __init__(self,entities=()):
        self.contracts = []
        self.valid = True
        self.context = queries.QueryContext
        if type(self.context) is not type:
            self.valid = False
        else:
            self._contract(self.context,'__init__','/helper/query_system/query.py','QueryContext.__init__')
        self.fields = {EqQuery:('idx_key','key'), InQuery:('idx_key','keys'),
            RangeQuery:('idx_key','min_value','max_value'),HasQuery:('idx_key',),
            AndQuery:('children',),OrQuery:('children',),DifferenceQuery:('base','excludes')}
        for cls in self.types:
            for name in ('eval','estimate'):
                self._contract(cls,name,'/helper/query_system/query.py',cls.__name__+'.'+name)
        for name in ('__init__','__iter__','__bool__','contains','count','update','union',
                     'intersection','difference','_ensure_capacity','_update_max_el'):
            self._contract(BitSet,name,'/helper/query_system/object_register.py','BitSet.'+name)
        for name in ('get_index_group','get_ordered_group'):
            self._contract(SimpleObjectIndexProvider,name,'/helper/query_system/object_register.py',
                           'SimpleObjectIndexProvider.'+name)
        self._contract(ObjectStorage,'__len__','/helper/query_system/object_register.py','ObjectStorage.__len__')
        for name in ('__init__','scope_map','bisect_left','bisect_right'):
            self._contract(SortedOrderedMap,name,'/helper/query_system/object_register.py','SortedOrderedMap.'+name)
        self.key_methods = tuple((name,getattr(IndexKey,name),getattr(getattr(IndexKey,name),'__code__',None))
                                 for name in ('__hash__','__eq__'))
        self.key_native = all(code is not None and code.co_filename == '<string>'
                              and fn.__qualname__ == 'IndexKey.'+name for name,fn,code in self.key_methods)
        self.entities = tuple(entities)
        for cls in self.entities:
            self._contract(cls,'__hash__','/helper/runtime_object.py','RuntimeObject.__hash__')
            self._contract(cls,'__eq__','/helper/runtime_object.py','RuntimeObject.__eq__')
        self.entity_keys = []
        for cls in self.entities:
            prop = getattr_static(cls,'key')
            origin = (_origin(prop.fget,'/'+cls.__module__.replace('.','/')+'.py',cls.__qualname__+'.key')
                      if type(prop) is property else None)
            if origin is None:
                self.valid = False
            else:
                self.entity_keys.append((cls,prop,origin[1]))
        self.enum_hash = Enum.__hash__
        self.enum_code = getattr(self.enum_hash,'__code__',None)

    def _contract(self,cls,name,suffix,qualified):
        function = getattr(cls,name)
        origin = _origin(function,suffix,qualified)
        if origin is None:
            self.valid = False
            return
        self.contracts.append((cls,name,function,getattr(function,'__code__',None),*origin))

    def intact(self):
        if (not self.valid or not self.key_native or queries.BitSet is not BitSet
                or registers.BitSet is not BitSet or BitSet.__new__ is not object.__new__
                or BitSet.__getattribute__ is not object.__getattribute__
                or IndexKey.__getattribute__ is not object.__getattribute__
                or queries.QueryContext is not self.context
                or registers.SortedOrderedMap is not SortedOrderedMap
                or SortedOrderedMap.__new__ is not object.__new__
                or SortedOrderedMap.__getattribute__ is not object.__getattribute__
                or not _plain_setter(SortedOrderedMap)
                or self.context.__new__ is not object.__new__
                or self.context.__getattribute__ is not object.__getattribute__
                or not _plain_setter(BitSet) or not _plain_setter(self.context)):
            return False
        for cls,name,fn,code,origin,origin_code in self.contracts:
            if (getattr(cls,name) is not fn or getattr(fn,'__code__',None) is not code
                    or origin.__code__ is not origin_code):
                return False
        for name,fn,code in self.key_methods:
            if getattr(IndexKey,name) is not fn or fn.__code__ is not code:
                return False
        for cls,prop,code in self.entity_keys:
            if (getattr(cls,'key') is not prop or prop.fget.__code__ is not code
                    or cls.__getattribute__ is not object.__getattribute__
                    or any('_key' in vars(parent) for parent in cls.__mro__)):
                return False
        return not any(name in vars(parent) for cls,names in (
            (BitSet,('_bits','_max_elements','_all_bits_mask')),
            (IndexKey,('name',)),(SimpleObjectIndexProvider,('_data','_ordered_keys')),
            (SortedOrderedMap,('_data',)),
            (ObjectStorage,('_id_to_obj','_free_ids')),(self.context,('storage','idx_provider')))
            for parent in cls.__mro__ for name in names)

    def pure_key(self,value):
        cls = type(value)
        if cls in self.scalar:
            return True
        if cls in self.entities:
            return type(vars(value).get('_key')) is str
        return (isinstance(value,Enum) and cls.__getattribute__ is object.__getattribute__
                and cls.__hash__ in (Enum.__hash__,int.__hash__,str.__hash__)
                and cls.__eq__ in (object.__eq__,int.__eq__,str.__eq__)
                and Enum.__hash__ is self.enum_hash and self.enum_code is not None
                and self.enum_hash.__code__ is self.enum_code
                and self.enum_code.co_filename.replace('\\','/').endswith('/enum.py')
                and not any('_name_' in vars(parent) for parent in cls.__mro__)
                and type(vars(value).get('_name_')) is str)

    def index_key(self,value):
        return type(value) is IndexKey and type(vars(value).get('name')) is str

    def tree(self,value,depth=0):
        cls = type(value)
        if cls not in self.fields or depth > 64 or cls.__getattribute__ is not object.__getattribute__:
            return False
        data = vars(value)
        if 'eval' in data or 'estimate' in data:
            return False
        schema = self.fields[cls]
        for name in schema:
            if (name not in data or any(hasattr(type(vars(parent).get(name)),'__get__')
                                        for parent in cls.__mro__)):
                return False
        if cls is EqQuery:
            return self.index_key(data['idx_key']) and self.pure_key(data['key'])
        if cls is InQuery:
            return (self.index_key(data['idx_key']) and type(data['keys']) is frozenset
                    and all(self.pure_key(key) for key in data['keys']))
        if cls is RangeQuery:
            return (self.index_key(data['idx_key'])
                and type(data['min_value']) in self.scalar and type(data['max_value']) in self.scalar)
        if cls is HasQuery:
            return self.index_key(data['idx_key'])
        if cls in (AndQuery,OrQuery):
            children = data['children']
        else:
            if not self.tree(data['base'],depth+1):
                return False
            children = data['excludes']
        return type(children) is tuple and all(self.tree(child,depth+1) for child in children)

    def evaluate(self,query,storage,provider):
        """Return a nonnegative integer, or None for the legacy dispatch."""
        if (type(query) not in self.fields or not self.intact() or not self.tree(query)
                or type(storage) is not ObjectStorage or type(provider) is not SimpleObjectIndexProvider
                or ObjectStorage.__getattribute__ is not object.__getattribute__
                or SimpleObjectIndexProvider.__getattribute__ is not object.__getattribute__):
            return None
        data = vars(provider)
        if ('get_index_group' in data or 'get_ordered_group' in data
                or type(data.get('_data')) is not dict or type(data.get('_ordered_keys')) is not set
                or not all(self.index_key(key) for key in data['_data'])
                or not all(self.index_key(key) for key in data['_ordered_keys'])
                or type(vars(storage).get('_id_to_obj')) is not list
                or type(vars(storage).get('_free_ids')) is not list):
            return None
        try:
            return _MaskContext(self,storage,provider).mask(query)
        except _Unsupported:
            return None


class _MaskContext:
    __slots__ = ('evaluator','storage','provider','groups')

    def __init__(self,evaluator,storage,provider):
        self.evaluator,self.storage,self.provider = evaluator,storage,provider
        self.groups = {}

    def group(self,key):
        saved = self.groups.get(key)
        if saved is not None:
            return saved
        values = self.provider.get_index_group(key)
        if type(values) is not dict:
            raise _Unsupported
        for name,bits in values.items():
            if not self.evaluator.pure_key(name) or type(bits) is not BitSet:
                raise _Unsupported
            data = vars(bits)
            if ('count' in data or type(data.get('_bits')) is not int or data['_bits'] < 0
                    or type(data.get('_max_elements')) is not int
                    or type(data.get('_all_bits_mask')) is not int):
                raise _Unsupported
        self.groups[key] = values
        return values

    def ranged(self,q):
        if q.idx_key not in self.provider._ordered_keys:
            raise KeyError(f"Index '{q.idx_key.name}' is not configured for range queries.")
        group = self.group(q.idx_key)
        if any(type(key) not in (str,int,float,bool,bytes) for key in group):
            raise _Unsupported
        keys = sorted(group)
        lo = 0 if q.min_value is None else bisect_left(keys,q.min_value)
        hi = len(keys) if q.max_value is None else bisect_right(keys,q.max_value)
        return (group[key] for key in keys[lo:hi])

    @staticmethod
    def union(values):
        mask = 0
        for value in values:
            mask |= value._bits
        return mask

    def estimate(self,q):
        cls = type(q)
        if cls is EqQuery:
            value = self.group(q.idx_key).get(q.key)
            return 0 if value is None else value._bits.bit_count()
        if cls is InQuery:
            group = self.group(q.idx_key)
            return min(len(self.storage),sum(v._bits.bit_count() for k in q.keys if (v:=group.get(k)) is not None))
        if cls is RangeQuery:
            return min(len(self.storage),sum(v._bits.bit_count() for v in self.ranged(q)))
        if cls is HasQuery:
            return self.union(self.group(q.idx_key).values()).bit_count()
        if cls is AndQuery:
            return min((self.estimate(c) for c in q.children),default=0)
        if cls is OrQuery:
            return min(len(self.storage),sum(self.estimate(c) for c in q.children))
        if not q.excludes:
            return self.estimate(q.base)
        largest = max(self.estimate(c) for c in q.excludes)
        return max(0,self.estimate(q.base)-largest)

    def mask(self,q):
        cls = type(q)
        if cls is EqQuery:
            value = self.group(q.idx_key).get(q.key)
            return 0 if value is None else value._bits
        if cls is InQuery:
            group = self.group(q.idx_key)
            return self.union(v for k in q.keys if (v:=group.get(k)) is not None)
        if cls is RangeQuery:
            return self.union(self.ranged(q))
        if cls is HasQuery:
            return self.union(self.group(q.idx_key).values())
        if cls in (AndQuery,OrQuery):
            if not q.children:
                return 0
            children = sorted(q.children,key=self.estimate) if cls is AndQuery else q.children
            result = self.mask(children[0])
            for child in children[1:]:
                value = self.mask(child)
                if cls is AndQuery:
                    if not result:
                        return result
                    result &= value
                else:
                    result |= value
            return result
        result = self.mask(q.base)
        if not result:
            return 0
        for child in q.excludes:
            value = self.mask(child)
            if not result:
                return result
            result &= ~value
        return result
