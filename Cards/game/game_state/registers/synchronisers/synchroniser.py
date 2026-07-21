from __future__ import annotations
from immutabledict import immutabledict
from abc import ABC, abstractmethod

from ....enums import *
from ...card import Card
from ...player import Player
from helper.query_system.object_register import RegisterSynchroniser

class CardRegisterSynchroniser(RegisterSynchroniser[Card]):
    pass

class PlayerRegisterSynchroniser(RegisterSynchroniser[Player]):
    pass
    
class Synchroniser:
    
    def __init__(self):
        self._synchronisers: immutabledict[RuntimeObjectType, RegisterSynchroniser] = immutabledict({
            RuntimeObjectType.CARD: CardRegisterSynchroniser(),
            RuntimeObjectType.PLAYER: PlayerRegisterSynchroniser()
        })
    
    def synchronise(self): ...