from __future__ import annotations
from collections.abc import Mapping, Iterable, Hashable, Set
from immutabledict import immutabledict
from typing import TYPE_CHECKING, override
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..player import Player


from helper.query_system.object_register import ObjectRegister, ObjectStorage, IndexKey, ObjectIndexProvider, BitSet, ObjectSyncronizer
from ..card import Card
from ...enums import *


class KeyWord:
    pass


IK_OWNER = IndexKey[Player]()
IK_CONTROLLER = IndexKey[Player]()
IK_CMC = IndexKey[int]()
IK_ZONE = IndexKey[ZoneType]()
IK_TYPE = IndexKey[CardType]()
IK_SUBTYPE = IndexKey[CardSubtype]()
IK_POWER = IndexKey[int]()
IK_TOUGHNESS = IndexKey[int]()
IK_KEYWORD = IndexKey[KeyWord]()

CARD_INDEX_KEYS: frozenset[IndexKey] = frozenset({
    IK_OWNER,
    IK_CONTROLLER,
    IK_CMC,
    IK_ZONE,
    IK_TYPE,
    IK_SUBTYPE,
    IK_POWER,
    IK_TOUGHNESS,
    IK_KEYWORD
})


class CardSynchronizer(ObjectSyncronizer[Card]):

    @override
    def synchronize(self, obj, storage, idx_provider):
        raise NotImplementedError()
 

class LayerEngine:
    pass 

class CardRegister(ObjectRegister[Card]):
    
    def __init__(self):
        self._storage: ObjectStorage[Card] = ObjectStorage[Card]()
        self._idx_provider: CardIndexProvider = CardIndexProvider[Card]()
        self._synchronizer: CardSynchronizer = CardSynchronizer() 
    



class CardIndexProvider(ObjectIndexProvider[Card]):

    def __init__(self):
        self._data: immutabledict[IndexKey[Hashable], dict[Hashable, BitSet]] = immutabledict({
            ik: {} for ik in CARD_INDEX_KEYS
        })


    @property
    @override
    def data(self):
        return self._data
    
    

