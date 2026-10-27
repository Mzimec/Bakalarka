"""Resume explicit scenario recipes using own recall and a fixed opponent.

This is a controlled-scenario bridge, not inference of a human's hidden choices.
A candidate world that does not reproduce every recorded own observation/event
is rejected. No history is discarded and no incompatible pool is partly used.
"""
from game.ai.information_set_search import SampledRootMismatch
from game.ai.search_simulator import SimulationBudgetExceeded
from game.ai.history_diagnostics import recall_mismatch
from game.ai.opening_reveal_evidence import observed_mulligans


class ScenarioWorldProvider:
    """!
    @brief Supply fresh worlds at a live decision without accepting a runtime State.

    world_factory(seed) creates a tracking simulator at the scenario origin. Past
    own actions come from player recall; past opponent actions follow the explicit
    policy. This only supports histories consistent with that policy and recipe.
    It does not condition beliefs on arbitrary opponent choices or resample worlds
    until a match is found. Composition assumptions must be supplied separately.
    """
    def __init__(self, world_factory, *, opponent_policy, max_prefix_advances=256):
        if not callable(world_factory) or not callable(opponent_policy):
            raise TypeError("Supply a scenario world factory and observation-only opponent policy")
        if type(max_prefix_advances) is not int or max_prefix_advances < 1:
            raise ValueError("max_prefix_advances must be a positive integer")
        self.world_factory, self.opponent_policy = world_factory, opponent_policy
        self.max_prefix_advances = max_prefix_advances

    def __call__(self, information):
        def sample(seed):
            return _ResumedScenario(self.world_factory(seed), information, self.opponent_policy, self.max_prefix_advances)
        return sample


class _ResumedScenario:
    """!
    @brief Replay an owned scenario to a supported information boundary.
    """
    def __init__(self, simulator, information, opponent_policy, max_advances):
        self.simulator, self.information = simulator, information
        self.opponent_policy, self.max_advances = opponent_policy, max_advances

    def start(self):
        """!
        @brief Initialize the owned scenario and expose its first supported boundary.
        """
        return resume_to_information(self.simulator, self.information, self.opponent_policy, self.max_advances)

    def advance_action(self, node, action):
        """!
        @brief Bind and execute one complete visible action in an isolated world.
        """
        return self.simulator.advance_action(node, action)


def resume_to_information(simulator, target, opponent_choose, max_advances, *, on_advance=None, start_node=None,
                          early_setup_check=True):
    """!
    @brief Shared strict history check for fixed-policy and sampled reconstructions.

    Own choices are interventions from recall, never predictions of a player policy.
    Check exposed event evidence even while the opponent is acting, so impossible
    prefixes stop early. A work cap is not evidence against the sampled world.
    """
    count = 0
    mulligans = observed_mulligans(target) if early_setup_check else {}
    def select(node):
        nonlocal count
        if node.cutoff:
            raise SimulationBudgetExceeded("Scenario prefix reached the simulator phase limit")
        prefix = node.recall(target.current.player).entries
        if prefix != target.recall.entries[:len(prefix)]:
            raise SampledRootMismatch("Scenario prefix differs from the player's recorded evidence",
                                      reason=recall_mismatch(prefix, target.recall.entries))
        if node.terminal:
            raise SampledRootMismatch("Scenario ended before the live decision", reason="terminal_before_target")
        info = node.player_information()
        if info.current.player == target.current.player:
            if len(prefix) == len(target.recall.entries):
                if info != target:
                    raise SampledRootMismatch("Scenario reached a different live decision", reason="current_decision")
                return None
            recorded = target.recall.entries[len(prefix)]
            action = next((a for a in info.actions if a._json == recorded._json), None)
            while action is None and recorded.kind == 'action' and info.candidate_model is not None and not info.menu_complete:
                limit = min(max(info.candidate_width + 1, info.candidate_width * 2), simulator.max_options)
                if limit <= info.candidate_width:
                    raise SimulationBudgetExceeded('Recorded own action exceeds the candidate reconstruction budget')
                node = simulator.expand_candidates(node, limit)
                info = node.player_information()
                action = next((a for a in info.actions if a._json == recorded._json), None)
            if recorded.kind != "action" or action is None:
                raise SampledRootMismatch("Recorded own choice is unavailable in the scenario", reason="own_action_unavailable")
        else:
            # History likelihoods retain the declared full opponent policy.
            # A proposal prefix must not assign zero likelihood to an observed
            # opponent action that lies later in the exhaustive stream.
            while info.candidate_model is not None and not info.menu_complete:
                limit = min(max(info.candidate_width + 1, info.candidate_width * 2), simulator.max_options)
                if limit <= info.candidate_width:
                    raise SimulationBudgetExceeded('Full opponent history menu exceeds the reconstruction budget')
                node = simulator.expand_candidates(node, limit)
                info = node.player_information()
            action = opponent_choose(info)
            if action not in info.actions:
                raise ValueError("History policy returned an action not offered to the opponent")
            if info.current.request_kind == "MulliganRequest" and info.current.player in mulligans:
                taken = info.current.observation()["decision_context"]["mulligans_taken"]
                expected = mulligans[info.current.player]
                # Sample from the original policy, then reject an impossible
                # path BEFORE executing redraws/bottom menus. Never force the
                # observed choice or remove its likelihood from the posterior.
                if taken > expected or action.describe()["take_mulligan"] != (taken < expected):
                    raise SampledRootMismatch("Opponent mulligan choice contradicts public final hand size",
                                              reason="opponent/mulligan_count")
        if count == max_advances:
            raise SimulationBudgetExceeded("Scenario prefix advance limit reached")
        if on_advance is not None:
            on_advance()
        count += 1
        return action

    follow = getattr(simulator, "follow_actions", None)
    if follow is not None:
        node = follow(select) if start_node is None else follow(select, node=start_node)
        # A terminal/cutoff runtime can finish without another controller call.
        if node.terminal or node.cutoff:
            select(node)
        return node
    # Other simulator implementations need only start/advance_action. Keep the
    # stepwise path as a differential reference and compatibility backend.
    node = simulator.start() if start_node is None else start_node
    while True:
        action = select(node)
        if action is None:
            return node
        node = simulator.advance_action(node, action)
