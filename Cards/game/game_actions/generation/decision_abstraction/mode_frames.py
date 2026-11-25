"""Resolve authored OR branches before taking their Cartesian product."""
from dataclasses import replace

from ...data_structs.action_node import AndActionNode, OrActionNode, EffectActionNode, ManaActionNode


def resolve_modes(graph, prefix=()):
    """Return a constrained native graph and its first unresolved OR path.

    Only reviewed immutable native nodes are rewritten. An extension keeps its
    original generator. Paths identify occurrences, not Python object identity.
    """
    selections = dict(prefix)
    pending = None
    def validate(node):
        if type(node) in (AndActionNode, OrActionNode):
            for child in node.children:
                validate(child)
        elif type(node) not in (EffectActionNode, ManaActionNode) and node is not None:
            raise TypeError('Custom action graph has no native mode continuation')
    validate(graph)
    def walk(node, path):
        nonlocal pending
        if type(node) is OrActionNode:
            if path in selections:
                index = selections[path]
                if type(index) is not int or not 0 <= index < len(node.children):
                    raise ValueError('Invalid mode branch')
                return walk(node.children[index], (*path, index))
            if pending is None:
                pending = path, node.children
            return node
        if type(node) is AndActionNode:
            return replace(node, children=tuple(walk(child, (*path, i)) for i, child in enumerate(node.children)))
        if type(node) not in (EffectActionNode, ManaActionNode) and node is not None:
            raise TypeError('Custom action graph has no native mode continuation')
        return node
    return walk(graph, ()), pending
