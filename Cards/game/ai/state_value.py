"""Cheap observation-only baseline for future search leaves, not a win model."""
from math import tanh


class StateValueEvaluator:
    """!
    @brief Score visible material and player resources with a local heuristic.
    """
    def permanent_value(self, card):
        """!
        @brief Estimate permanent material from visible characteristics and abilities.
        """
        if not card.get("identity_visible", True):
            return 1.0  # Explicit neutral prior for an unidentified permanent.
        keywords = set(card.get("keywords", ()))
        value = 0.5
        if "LAND" in card.get("types", ()):
            value += 1.0
        if "CREATURE" in card.get("types", ()):
            power = max(0, card.get("power") or 0)
            toughness = max(0, (card.get("toughness") or 0) - card.get("damage_marked", 0))
            if "can't attack" not in keywords and "defender" not in keywords:
                value += 0.6 * power + 0.15 * toughness
            if "can't block" not in keywords:
                value += 0.3 * power + 0.15 * toughness
        # Noncombat abilities retain value even on a pacified creature.
        value += 0.5 * sum(not a.get("key", "").startswith("cast:")
                           for a in card.get("abilities", ()))
        value += 0.5 * len(card.get("triggers", ()))
        value += 0.5 * len(card.get("static_abilities", ()) or ())
        return value

    def evaluate(self, view):
        """!
        @brief Score the supplied player-visible position from its observer perspective.
        """
        players, own = view["players"], view["self"]
        survivors = [p for p in players if not p.get("has_lost") and p["life"] > 0 and p.get("poison_counters", 0) < 10]
        if not survivors:
            return 0.0
        if not any(p["id"] == own for p in survivors):
            return -1.0
        if len(survivors) == 1:
            return 1.0
        score = 0.0
        opponents = len(players) - 1
        for player in players:
            sign = 1.0 if player["id"] == own else -1.0 / opponents
            score += sign * (0.1 * player["life"] - 0.6 * player.get("poison_counters", 0)
                             + 0.8 * player["hand_size"] + 0.1 * sum(player.get("mana", {}).values()))
        for card in view["battlefield"]:
            sign = 1.0 if card["controller"] == own else -1.0 / opponents
            score += sign * self.permanent_value(card)
        return tanh(score / 15.0)
