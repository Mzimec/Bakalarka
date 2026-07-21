from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from ..data_structs.action_node import ActionNode, ActionNodeOption



class ActionNodeOptionGenerator(ABC):

    @abstractmethod
    def generate(
        self,
        action_node: ActionNode
    ) -> Iterator[ActionNodeOption]: ...


class FullActionNodeOptionGenerator(ActionNodeOptionGenerator):

    @override
    def generate(self, action_node) -> Iterator[ActionNodeOption]:
        yield from action_node.generate_options()
