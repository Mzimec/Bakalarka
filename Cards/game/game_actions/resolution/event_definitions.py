"""Reviewed native event payloads shared by capture and search projection.

These are semantic fields, not a dump of an event's runtime graph. Unknown
extension keys have no implicit public adapter. Zone events require a frozen
viewer projection; their raw card/LKI payload must never reach a policy.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class EventDefinition:
    key: str
    public_fields: tuple[str, ...] = ()
    frozen_zone_projection: bool = False
    ability_field: str | None = None
    private_fields: tuple[str, ...] = ()


def _event(key, *fields, **kwargs):
    return EventDefinition(key, fields, **kwargs)


EVENT_DEFINITIONS = MappingProxyType({event.key: event for event in (
    *(_event(key, frozen_zone_projection=True) for key in
      ("card_moved", "card_drawn", "library_shuffled")),
    _event("spell_cast", ability_field="ability"),
    _event("ability_activated", ability_field="ability"),
    _event("phase_started", "phase", "turn"),
    _event("damage_dealt", "target_object", "amount", "combat", "life_loss", "source_revision", "target_revision"),
    _event("life_gained", "amount", "player"),
    _event("life_lost", "amount", "target_object"),
    _event("player_lost", "reason"),
    _event("attacker_declared", "defender", "attacker"),
    _event("attackers_declared", "attackers"),
    _event("blocker_declared", "attacker", "blocker"),
    _event("attacker_blocked", "blockers"),
    _event("blockers_declared", "blockers"),
    _event("land_played", "lands_played"),
    _event("counter_placed", "counter", "amount"),
    _event("temporary_effect_applied", "targets"),
    _event("skip_next_untap", "player"),
    _event("spell_countered", "counter_source"),
    _event("mana_added", "mana", "amount"),
    _event("mana_paid", "payment"),
    _event("life_paid", "amount"),
    _event("loyalty_paid", "amount"),
    _event("replacement_installed", "replacement"),
    _event("attached", "host"), _event("detached", "host"),
    _event("scry", "amount", private_fields=("top", "bottom")),
    *(_event(key) for key in ("card_tapped", "card_untapped", "draw_failed",
        "buff_applied", "state_trigger_check", "reflexive_trigger_created",
        "priority_passed", "player_conceded")),
)})
