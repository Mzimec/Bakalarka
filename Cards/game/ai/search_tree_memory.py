"""Connected, bounded actor-information subtree. No worlds or live bindings."""
from dataclasses import dataclass, field
from heapq import heappush, heappop
from itertools import count
from sys import getsizeof


@dataclass(slots=True)
class EdgeStatistics:
    """!
    @brief Compact completed visit count and root-player value sum for an edge.
    @var visits Number of completed trajectory samples.
    @var total Sum of completed values from the root player perspective.
    """
    visits: int = 0
    total: float = 0.0


class EdgeTable(dict):
    """!
    @brief Compact edge counters with a maintained parent visit count.
    """
    def __init__(self, rows=()):
        super().__init__(rows)
        self.visits = sum(s.visits for s in self.values())

    def record(self, action, value):
        """!
        @brief Accumulate a completed action or decision in the owner statistics.
        """
        row = self[action]
        row.visits += 1
        row.total += value
        self.visits += 1


@dataclass(frozen=True)
class TreeRow:
    """!
    @brief Portable immutable snapshot of one information node and its action statistics.
    @var information Immutable actor observation, recall and offered actions.
    @var statistics Detached completed action visits and value totals.
    @var probabilities Complete offered-action prior probabilities.
    @var horizon Remaining depth/extensions under which stored visits were collected.
    """
    information: object
    statistics: tuple
    probabilities: tuple
    horizon: tuple = ()


@dataclass(slots=True)
class RetainedNode:
    """!
    @brief Connected actor node retaining structure, priors and compatible statistics.
    @var information Immutable actor observation, recall and offered actions.
    @var edges Owned mutable completed action counters.
    @var probabilities Complete offered-action prior probabilities.
    @var horizon Remaining depth/extensions under which stored visits were collected.
    @var children Retained child information keys linked through parent actions.
    @var hierarchy Optional semantic prefix selection structure for this menu.
    @var candidates Optional retained deterministic candidate order.
    """
    information: object
    edges: EdgeTable
    probabilities: dict = field(default_factory=dict)
    horizon: tuple = ()
    children: dict = field(default_factory=dict)  # child key -> parent action
    hierarchy: object = None
    candidates: object = None

    def row(self):
        """!
        @brief Export one immutable information-node statistics snapshot.
        """
        return TreeRow(self.information,tuple((a,s.visits,s.total) for a,s in self.edges.items()),
                       tuple(self.probabilities.items()),self.horizon)


def _charge(value, seen, ceiling):
    """!
    @brief Count retained Python containers/slots, not allocator arenas or RSS.
    """
    stack, total = [value],0
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj,type) or callable(obj):
            continue
        seen.add(id(obj))
        total += getsizeof(obj)
        if total > ceiling:
            break
        if isinstance(obj,dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj,(tuple,list,set,frozenset)):
            stack.extend(obj)
        if hasattr(obj,'__dict__'):
            stack.append(vars(obj))
        for slot in getattr(type(obj),'__slots__',()):
            if slot not in ('__dict__','__weakref__') and hasattr(obj,slot):
                stack.append(getattr(obj,slot))
    return total


class SearchTreeMemory:
    """!
    @brief Bound connected actor subtrees under session, belief and horizon checks.
    """
    def __init__(self):
        self.clear()

    def clear(self):
        """!
        @brief Release retained state and reset compatibility and resource accounting.
        """
        self.context = self.signature = self.root_key = None
        self.nodes = {}
        self.statistics_compatible = False
        self.last_reason = 'empty'
        self.pending_reason = None
        self.charged_bytes = self.pruned_nodes = 0

    @property
    def rows(self):
        # Detached compatibility/export form, not the search hot path.
        """!
        @brief Expose detached compatibility rows rather than mutable retained counters.
        """
        return {k:n.row() for k,n in self.nodes.items()}

    def invalidate_statistics(self, reason='belief_changed'):
        """!
        @brief Retain actor facts/pure priors; no outcome samples cross a new belief.
        """
        for node in self.nodes.values():
            node.edges = EdgeTable((a,EdgeStatistics()) for a in node.edges)
            node.hierarchy = None
        self.statistics_compatible = False
        self.pending_reason = reason

    def load(self, context, signature, root, *, horizon=None, rolling=False, export=True):
        """!
        @brief Reroot retained structure under session, information, belief and horizon checks.
        """
        previous = self.nodes.get(root.fast_key)
        matches = previous is not None and previous.information == root
        if previous is not None and root.candidate_model is not None:
            old = previous.information
            matches = (old.current == root.current and old.recall == root.recall
                       and old.candidate_model == root.candidate_model
                       and set(root.actions) <= set(old.actions)
                       and (not root.menu_complete or set(root.actions) == set(old.actions)))
        reason = ('no_context' if context is None else 'empty' if self.context is None else
                  'session_changed' if context is not self.context else
                  'signature_changed' if signature!=self.signature else
                  'root_missing' if previous is None else 'information_changed' if not matches else None)
        if reason:
            self.clear()
            self.last_reason = reason
            return ()
        prefix, retained, stack = root.recall.entries,{},[root.fast_key]
        while stack:
            key = stack.pop()
            if key in retained or key not in self.nodes:
                continue
            node = self.nodes[key]
            info = node.information
            if info.current.player != root.current.player or info.recall.entries[:len(prefix)]!=prefix:
                continue
            retained[key] = node
            stack.extend(node.children)
        self.pruned_nodes = len(self.nodes)-len(retained)
        self.nodes, self.root_key = retained,root.fast_key
        compatible = horizon is None or rolling or previous.horizon==horizon
        self.statistics_compatible = compatible
        self.last_reason = (self.pending_reason or 'reused') if compatible else 'horizon_changed'
        self.pending_reason = None
        if not compatible:
            # Menus/priors/ordering survive; horizon-dependent N/W do not.
            for node in retained.values():
                node.edges = EdgeTable((a,EdgeStatistics()) for a in node.edges)
                node.hierarchy = None
        return tuple(n.row() for n in retained.values()) if export and compatible else ()

    def commit(self, context, signature, nodes, root, limit, byte_limit=16*1024*1024):
        """!
        @brief Retain connected nodes within the configured node and byte limits.
        """
        if context is None or not limit or not byte_limit or root not in nodes:
            self.clear()
            return
        selected, heap, serial, seen, charged = {},[],count(),set(),getsizeof({})
        heappush(heap,(0,next(serial),root))
        while heap and len(selected)<limit:
            _,_,key = heappop(heap)
            if key in selected or key not in nodes:
                continue
            node = nodes[key]
            size = _charge((key,node),seen,byte_limit-charged)+64
            if size+charged>byte_limit:
                break
            selected[key] = node
            charged += size
            for child in node.children:
                if child in nodes and child not in selected:
                    heappush(heap,(-nodes[child].edges.visits,next(serial),child))
        for node in selected.values():
            node.children = {k:a for k,a in node.children.items() if k in selected}
        self.context, self.signature, self.root_key = context,signature,root
        self.nodes = selected
        self.charged_bytes = charged if selected else 0
        self.pruned_nodes = len(nodes)-len(selected)

    def save(self, context, signature, rows, limit, *, links=(), root=None, byte_limit=16*1024*1024):
        """!
        @brief Store detached rows as a bounded connected actor-information subtree.
        """
        nodes = {r.information.fast_key:RetainedNode(r.information,
            EdgeTable((a,EdgeStatistics(n,w)) for a,n,w in r.statistics),dict(r.probabilities),r.horizon) for r in rows}
        for parent,action,child in links:
            if parent in nodes and child in nodes:
                nodes[parent].children[child] = action
        self.commit(context,signature,nodes,root or next(iter(nodes),None),limit,byte_limit)

    def export(self):
        """!
        @brief Return detached retained rows, links and root identity for transport.
        """
        return (tuple(n.row() for n in self.nodes.values()),
                tuple((k,a,c) for k,n in self.nodes.items() for c,a in n.children.items()),self.root_key)
