"""Bitset lookup of immutable continuous-effect definitions.

Entries own registry identity so frozen effects can be shared without mutating them.
"""
from helper.runtime_object import RuntimeObject
from helper.query_system.object_register import InMemoryObjectRegister, IndexKey, IndexUpdate, RegisterUpdateContext
from helper.query_system.query import HasQuery

IK_EFFECT_END = IndexKey("effect.end_timestamp")
IK_EFFECT_MOMENT = IndexKey("effect.end_player_moment")
IK_EFFECT_MOMENT_PLAYER = IndexKey[int]("effect.moment_player")
IK_EFFECT_DYNAMIC_DURATION = IndexKey[bool]("effect.dynamic_duration")
IK_EFFECT_KEY = IndexKey[str]("effect.key")
IK_EFFECT_SOURCE = IndexKey[str]("effect.source_key")
IK_EFFECT_LAYER = IndexKey("effect.layer")
IK_EFFECT_DURATION = IndexKey[type]("effect.duration")
IK_EFFECT_STATIC = IndexKey[bool]("effect.static")


class EffectEntry(RuntimeObject):
    def __init__(self, effect):
        super().__init__()
        self.effect = effect

    @property
    def key(self):
        return self.effect.key


class EffectRegister:
    def __init__(self):
        self._register = InMemoryObjectRegister((
            IK_EFFECT_KEY, IK_EFFECT_SOURCE, IK_EFFECT_LAYER,
            IK_EFFECT_DURATION, IK_EFFECT_STATIC, IK_EFFECT_END,
            IK_EFFECT_MOMENT, IK_EFFECT_MOMENT_PLAYER, IK_EFFECT_DYNAMIC_DURATION,
        ), (IK_EFFECT_END, IK_EFFECT_MOMENT))
        self._entries = {}

    def add(self, effect):
        from ..layers import modifier_layer

        if effect.key in self._entries:
            raise ValueError(f"Duplicate effect key: {effect.key}")
        source = effect.definition.source
        from ..modifier import TimeStampDuration, GameMomentDuration, PermanentDuration
        duration = effect.duration
        moment = duration.end_game_moment if type(duration) is GameMomentDuration else None
        values = {
            IK_EFFECT_END: ({duration.end_time_stamp} if type(duration) is TimeStampDuration else set()),
            IK_EFFECT_MOMENT: ({(moment.moment.taken_turns, moment.moment.phase.value)} if moment else set()),
            IK_EFFECT_MOMENT_PLAYER: ({moment.player_idx} if moment else set()),
            IK_EFFECT_DYNAMIC_DURATION: {type(duration) not in (TimeStampDuration, GameMomentDuration, PermanentDuration)},
            IK_EFFECT_KEY: {effect.key},
            IK_EFFECT_SOURCE: {source.key} if source is not None else set(),
            IK_EFFECT_LAYER: {modifier_layer(stat, mod)
                              for stat, mods in effect.modifiers.items() for mod in mods},
            IK_EFFECT_DURATION: {type(effect.duration)},
            IK_EFFECT_STATIC: {effect.definition.static_ability_key is not None},
        }
        entry = EffectEntry(effect)
        self._register.add(entry, RegisterUpdateContext({
            key: IndexUpdate((), value) for key, value in values.items()
        }))
        self._entries[effect.key] = (entry, values)

    def remove(self, key):
        entry, values = self._entries.pop(key)
        self._register.remove(entry, RegisterUpdateContext({
            index: IndexUpdate(value, ()) for index, value in values.items()
        }))

    def expiration_candidates(self, state):
        """Use time indexes; unknown duration implementations retain exact validation."""
        from helper.query_system.query import EqQuery, RangeQuery, DifferenceQuery, InQuery, HasQuery
        query = RangeQuery(IK_EFFECT_END, max_value=state.time_stamp) | EqQuery(IK_EFFECT_DYNAMIC_DURATION, True)
        valid_players = frozenset(range(len(state.players)))
        query |= DifferenceQuery(HasQuery(IK_EFFECT_MOMENT_PLAYER),
                                 (InQuery(IK_EFFECT_MOMENT_PLAYER, valid_players),))
        indexed_players = self._register.idx_provider.get_index_group(IK_EFFECT_MOMENT_PLAYER)
        for index in indexed_players:
            if index not in valid_players:
                continue
            player = state.players[index]
            player_query = EqQuery(IK_EFFECT_MOMENT_PLAYER, index)
            if player.is_alive:
                moment = player.moment
                player_query &= RangeQuery(IK_EFFECT_MOMENT, max_value=(moment.taken_turns, moment.phase.value))
            query |= player_query
        return self.query(query)

    def query(self, query=None):
        return tuple(entry.effect for entry in self._register.query(
            HasQuery(IK_EFFECT_KEY) if query is None else query
        ))
