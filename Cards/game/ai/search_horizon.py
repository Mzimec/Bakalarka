"""Shared boundary for scoring settled search leaves, not rule execution."""


def quiet_priority(request_kind, view):
    """!
    @brief Check whether the priority boundary is settled for leaf evaluation.
    """
    if request_kind != "PriorityDecisionRequest" or view["pending_abilities"]:
        return False
    combat = view.get("combat", {})
    return not (combat.get("active") and combat.get("attackers")
                and not combat.get("regular_damage_done"))
