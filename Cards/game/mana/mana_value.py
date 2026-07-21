from __future__ import annotations
from dataclasses import dataclass
from abc import ABC
from typing import TYPE_CHECKING
from immutabledict import immutabledict
from collections.abc import Mapping, Generator

if TYPE_CHECKING:
    from ..game_actions.data_structs.ability import SubAbilityDefinition
    
from ..game_actions.data_structs.action_node import ManaActionNode
from ..enums import *
from helper.dict_helper import MutablePositiveCounterMapping, MutableCounterMapping


def generate_mana_options_from_req_pool(mana_req: ManaRequirementBase, mana_pool: ManaPoolBase) -> Generator[ImmutableManaPool, None, None]:

    def _recursive_step(idx: int) -> Generator[ImmutableManaPool, None, None]:

        def _generate_choices(key_idx: int, remaining: int, possible: int) -> Generator[ImmutableManaPool, None, None]:
            if remaining == 0:
                yield ImmutableManaPool(mp)
                return
                
            if remaining > possible:
                return
                
            m = key[key_idx]

            if key_idx == len(key) -1:
                mp.add_pair(m, remaining)
                yield ImmutableManaPool(mp)
                mp.remove_pair(m, remaining)
                return
                
            available = free.get(m, 0)
            maximum = min(available, remaining)

            for x in range(maximum + 1):
                mp.add_pair(m, x)
                yield from _generate_choices(key_idx + 1, remaining - x, possible - available)
                mp.remove_pair(m, x)


        if idx >= len(keys):
            yield ImmutableManaPool(payment)
            return
            
        key = tuple(sorted(keys[idx], key=lambda k: free.get(k, 0)))
        amount = mana_req[keys[idx]]
        mp = ManaPool()
        possible = sum(free.get(i, 0) for i in key)

        for choice in _generate_choices(0, amount, possible):
            payment.add(choice)
            free.remove(choice)

            yield from _recursive_step(idx + 1)

            payment.remove(choice)
            free.add(choice)


    if frozenset() in mana_req:
        return
        
    if mana_req.cmc > mana_pool.amount():
        return
        
    payment: ManaPool = ManaPool()
    free: ManaPool = ManaPool(mana_pool)

    for mana in ManaType:
        req_amount = mana_req.get(frozenset({mana}), 0)
        if req_amount > free.get(mana, 0):
            return
        payment.add_pair(mana, req_amount)
        free.remove_pair(mana, req_amount)

    keys: list[frozenset[ManaType]] = []
    for k in mana_req.keys():
        if len(k) > 1:
            keys.append(k)
        
    keys.sort(key=lambda k: len(k))

    yield from _recursive_step(0)


class ManaPoolBase(Mapping[ManaType, int], ABC):

    def amount(self) -> int:
        return sum(self.values())


class ManaPool(MutablePositiveCounterMapping[ManaType], ManaPoolBase):        

    def to_immutable(self) -> ImmutableManaPool:
        return ImmutableManaPool(self)


class ImmutableManaPool(immutabledict[ManaType, int], ManaPoolBase):

    def to_mutable(self) -> ManaPool:
        return ManaPool(self)


class ManaRequirementBase(Mapping[frozenset[ManaType], int], ABC):

    def cmc(self) -> int:
        return sum(self.values())


class ManaRequirement(MutablePositiveCounterMapping[frozenset[ManaType]], ManaRequirementBase):
    
    def to_immutable(self) -> ImmutableManaRequirement:
        return ImmutableManaRequirement(self)


class ImmutableManaRequirement(immutabledict[frozenset[ManaType], int], ManaRequirementBase):

    def to_mutable(self) -> ManaRequirement:
        return ManaRequirement(self)

@dataclass(frozen=True)
class ManaSymbol(ABC):
    
    cmc: int = 1
  

@dataclass(frozen=True)
class GeneralisedSymbol(ManaSymbol):

    subdef: SubAbilityDefinition


@dataclass(frozen=True)
class DeterministicSymbol(ManaSymbol):

    allowed: frozenset[ManaType]



class ManaValueBase(Mapping[ManaSymbol, int], ABC):
    
    def cmc(self) -> int:
        return sum(k.cmc * v for k, v in self.items())
    
    def to_subability_defs(self) -> tuple[SubAbilityDefinition, ...]:        
        deter_mreq: ManaRequirement = ManaRequirement()
        subdefs: list[SubAbilityDefinition] = []
        
        for k, v in self.items():
            if isinstance(k, DeterministicSymbol):
                deter_mreq.add({k.allowed: v})
            elif isinstance(k, GeneralisedSymbol):
                for _ in range(v):
                    subdefs.append(k.subdef)
        
        if len(deter_mreq) > 0:
            subdefs.append(
                SubAbilityDefinition(
                    action_node=ManaActionNode(deter_mreq.to_immutable())
                )
            )

        return tuple(subdefs)

            
class ManaValue(MutableCounterMapping[ManaSymbol], ManaValueBase):
    
    def to_immutable(self) -> ImmutableManaValue:
        return ImmutableManaValue(self)


class ImmutableManaValue(immutabledict[ManaSymbol, int], ManaValueBase):

    def to_mutable(self) -> ManaValue:
        return ManaValue(self)
    


    