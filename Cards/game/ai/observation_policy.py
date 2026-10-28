"""Deterministic opponent prior over PlayerInformation, never a sampled State.

Scores are local estimates, not simulated outcomes or win probabilities. The
engine supplies legality; unsupported choices remain offered with an explicit
fallback score. There is no persistent state to contaminate another rollout.
"""
from dataclasses import dataclass
from collections.abc import Mapping

from game.ai.search_actions import PlayerAction
from game.ai.state_value import StateValueEvaluator


@dataclass(frozen=True)
class ActionRating:
    """!
    @brief Offered action with a heuristic score and explicit support reasons.
    @var action Complete offered player-visible action.
    @var score Local heuristic utility, not an outcome probability.
    @var supported Whether reviewed semantics support this rating.
    @var reasons Explicit diagnostic reasons for this rating.
    """
    action: PlayerAction
    score: float
    supported: bool
    reasons: tuple[str, ...]


def _pairs(value):
    return value.get("mapping", ()) if isinstance(value, Mapping) else ()


def remembered_counter_sources(info, view):
    """!
    @brief Only still-known hand locations, matched to previously visible rule data.

    Seeing a card once does not imply it remains in hand. Unknown movement/shuffle
    invalidation is owned by PlayerKnowledge, not re-inferred here. Names without a
    previous visible description do not magically provide a deck model.
    """
    if not any(r['zone'] == 'HAND' and r['owner'] != view['self']
               for r in (view.get('knowledge') or {}).get('known_locations', ())):
        return ()
    return info.recall.knowledge.counter_sources(view)


class ObservationOpponentPolicy:
    """!
    @brief Composable observation-only prior for InformationSetSearch.opponent_policy.

    Call rank(info) for deterministic scores/reasons, or policy(info) to select an
    offered action. Ties prefer pass and then canonical action content, independent
    of option enumeration. This policy does not prune the root search menu.
    """
    def __init__(self, evaluator=None, *, memory=None):
        self.evaluator = evaluator if evaluator is not None else StateValueEvaluator()
        self.memory = memory

    def clear_memory(self):
        """!
        @brief Release exact pure ratings after a session or evaluator change.
        """
        if self.memory is not None:
            self.memory.clear()

    def rank(self, info):
        """!
        @brief Rate offered actions from the actor view with deterministic tie breaks.
        """
        if not info.actions:
            raise ValueError("Opponent policy requires offered actions")
        # Custom evaluators keep their historic detached-dict input contract.
        view = (info.current.readonly() if type(self.evaluator) is StateValueEvaluator
                else info.current.observation())
        if view.get("self") != info.current.player:
            raise ValueError("Observation must belong to the acting player")
        remembered = remembered_counter_sources(info, view)
        if self.memory is not None and type(self) is ObservationOpponentPolicy and type(self.evaluator) is StateValueEvaluator:
            return self.memory.rank(info, remembered,
                lambda canonical, counter_present: _Scorer(canonical, self.evaluator, counter_present))
        else:
            # Custom evaluators/policies have no reviewed input-equivalence
            # contract. They always receive the original complete observation.
            scorer = _Scorer(view, self.evaluator, remembered)
            rows = [scorer.rate(a) for a in info.actions]
        return tuple(sorted(rows, key=lambda r: (-r.score, r.action.kind != "PassPriorityAction",
                                                r.action.sort_key)))

    def __call__(self, info):
        return self.rank(info)[0].action


class _Scorer:
    """!
    @brief Score actions using one detached player view and remembered evidence.
    """
    def __init__(self, view, evaluator, remembered_answers):
        self.view, self.own, self.evaluator = view, view["self"], evaluator
        self.cards = {c["id"]: c for z in ("hand", "battlefield", "stack", "graveyard", "exile")
                      for c in view.get(z, ())}
        self.players = {p["id"]: p for p in view.get("players", ())}
        self.remembered_answers = remembered_answers

    def card(self, reference):
        """!
        @brief Read or project a visible card while respecting its zone incarnation.
        """
        card = self.cards.get((reference or {}).get("card"), {})
        if reference and reference.get("zone_revision", card.get("zone_revision")) != card.get("zone_revision"):
            return {}
        return card

    def sign(self, player):
        """!
        @brief Return the utility sign associated with the referenced player.
        """
        return 1 if player == self.own else -1

    def value(self, card):
        """!
        @brief Compute the configured value for the supplied visible position or card.
        """
        return self.evaluator.permanent_value(card) if card else 0.0

    def damage(self, target, amount):
        """!
        @brief Estimate damage utility from visible recipients and reviewed keywords.
        """
        if "player" in target:
            player = self.players.get(target["player"], {})
            return -self.sign(target["player"]) * (1000 if amount >= player.get("life", float("inf")) else 0.2 * amount)
        card = self.card(target)
        if not card:
            return 0.0
        keywords = card.get("keywords", ())
        lethal = amount >= (card.get("toughness") or float("inf")) - card.get("damage_marked", 0)
        value = self.value(card) if lethal and "indestructible" not in keywords else 0.05 * amount
        return -self.sign(card.get("controller")) * value

    def effect_value(self, feature, targets, source, controller, depth=0):
        """!
        @brief Estimate a reviewed declarative effect without executing engine rules.
        """
        kind = feature.get("kind", "unknown")
        sign = self.sign(controller)
        if kind == "damage":
            return sum(self.damage(t, feature["amount"] * count) for t, count in targets)
        if kind == "destroy":
            return sum(-self.sign(c.get("controller")) * self.value(c)
                       for t, _ in targets if (c := self.card(t)) and "indestructible" not in c.get("keywords", ()))
        if kind == "destroy_creatures":
            return sum(-self.sign(c["controller"]) * self.value(c) for c in self.view["battlefield"]
                       if "CREATURE" in c.get("types", ()) and "indestructible" not in c.get("keywords", ()))
        if kind == "draw":
            count = feature["amount"]
            return sign * (-1000 if count > self.players.get(controller, {}).get("library_size", float("inf")) else 1.1 * count)
        if kind == "gain_life":
            return sign * 0.15 * feature["amount"]
        if kind == "move_source":
            return sign * self.value(source) if feature["destination"] == "BATTLEFIELD" else 0.0
        if kind == "attach":
            restrictions = source.get("attachment_restrictions", ())
            if not restrictions:
                return 0.0
            value = 0.0
            for target, _ in targets:
                card = self.card(target)
                changed = {**card, "keywords": sorted(set(card.get("keywords", ())) | set(restrictions))}
                value -= self.sign(card.get("controller")) * (self.value(card) - self.value(changed))
            return value
        if kind == "counter" and depth < 4:
            total = 0.0
            for target, _ in targets:
                card = self.card(target)
                if not card or "can't be countered" in card.get("keywords", ()):
                    continue
                pending = next((p for p in self.view["pending_abilities"]
                                if (p.get("source") or {}).get("card") == card["id"]
                                and p.get("ability", "") == "cast:" + card["name"]), None)
                if pending is not None:
                    total -= self.pending_value(pending, depth + 1)
            return total
        return 0.0

    def pending_value(self, item, depth):
        """!
        @brief Estimate an unresolved visible stack item from effects and bindings.
        """
        source = self.card(item.get("source"))
        value, understood = 0.0, False
        for binding in (item.get("effects") or {}).get("sequence", ()):
            feature = binding["effect"].get("features", {"kind": "unknown"})
            slot = feature.get("slot")
            targets = [(t["object"], t["value"]) for group in (item.get("targets") or {}).get(slot, {}).values()
                       for t in group] if slot else []
            value += self.effect_value(feature, targets, source, item["controller"], depth)
            understood |= feature["kind"] not in {"unknown", "move_source", "tap_source"} or feature.get("destination") == "BATTLEFIELD"
        return value if understood else self.sign(item["controller"]) * max(1, source.get("mana_value", 0))

    def ability(self, data):
        """!
        @brief Describe or score a complete visible ability and explicit parameters.
        """
        plan = data.get("effect") or {}
        source = self.card(data.get("source"))
        bindings = plan.get("effects", ())
        value, supported = 0.0, bool(bindings)
        for binding in bindings:
            feature = binding.get("features", {"kind": "unknown"})
            slots = binding.get("slots", ())
            targets = [(ref, count) for slot, groups in _pairs(plan.get("targets"))
                       for runtime, group in _pairs(groups)
                       if any(slot == pair[0] and runtime == pair[1] for pair in slots)
                       for ref, count in _pairs(group)]
            value += self.effect_value(feature, targets, source, data["controller"]["player"])
            supported &= feature["kind"] != "unknown" and not (feature["kind"] == "attach" and not source.get("attachment_restrictions"))
        mana = (data.get("cost") or {}).get("mana") or {}
        paid = sum(n for _, n in _pairs(mana.get("payment")))
        value -= 0.15 * paid + 0.3 * mana.get("life_payment", 0)
        casting = (data.get("ability_key") or "").startswith("cast:")
        if casting:
            value -= 0.8  # Spending one card rather than a repeatable activation.
        reasons = ["visible_effects" if supported else "partial_or_unknown_effects"]
        # An explicit, modest risk prior, not a proof that the remembered answer
        # is currently payable. Never stall a lethal attempt on this estimate.
        if casting and self.remembered_answers and value < 100:
            value -= 0.25 * source.get("mana_value", 0)
            reasons.append("remembered_counter_in_hand")
        return value, supported, tuple(reasons)

    def combat(self, data, attack):
        """!
        @brief Estimate consequences of a complete attack or block declaration.
        """
        declarations = _pairs(data["declarations"])
        if attack:
            possible = self.view.get("decision_context", {}).get("possible_blocks")
            if possible is None:
                return -0.25, False, ("missing_blocking_context",)
            value, direct = 0.0, {}
            for attacker, defender in declarations:
                card = self.card(attacker)
                blockers = [self.card(p["blocker"]) for p in possible if p["attacker"]["card"] == attacker["card"]]
                power = max(0, card.get("power") or 0)
                if not blockers:
                    direct[defender.get("player")] = direct.get(defender.get("player"), 0) + power
                    value += 0.2 * power
                else:
                    # Conservative pair estimate; it does not solve assignments,
                    # trample, first strike, triggers or hidden combat tricks.
                    trade = min((self.value(b) if power >= (b.get("toughness") or 1) else 0)
                                - (self.value(card) if (b.get("power") or 0) >= (card.get("toughness") or 1) else 0)
                                for b in blockers)
                    value += min(0.2 * power, trade)
            value += sum(1000 for player, damage in direct.items()
                         if player in self.players and damage >= self.players[player]["life"])
        else:
            groups = {}
            for blocker, attacker in declarations:
                groups.setdefault(attacker["card"], []).append(self.card(blocker))
            value, damage = 0.0, 0
            for identity in self.view.get("combat", {}).get("attackers", ()):
                card = self.cards.get(identity, {})
                blockers = groups.get(identity, ())
                if not blockers:
                    damage += max(0, card.get("power") or 0)
                    continue
                if sum(max(0, b.get("power") or 0) for b in blockers) >= (card.get("toughness") or 1):
                    value += self.value(card)
                value -= sum(self.value(b) for b in blockers if (card.get("power") or 0) >= (b.get("toughness") or 1))
            value -= 1000 if damage >= self.players[self.own]["life"] else 0.2 * damage
        return value, True, ("approximate_combat_trade",)

    def rate(self, action):
        """!
        @brief Return local utility, support status and reasons for an offered action.
        """
        data, kind = action.readonly(), action.kind
        if kind == "PassPriorityAction":
            result = 0.0, True, ("pass",)
        elif kind == "ConcedeAction":
            result = -1000000.0, True, ("avoid_concession",)
        elif kind == "LandPlayAction":
            result = 3.0, True, ("develop_mana",)
        elif kind == "AbilityAction":
            result = self.ability(data)
        elif kind in ("PrioritySourceOption", "AbilityConstructionOption"):
            # A legal witness is an inexpensive proposal prior, not a claim
            # that it is the best target/parameter completion for this source.
            result = self.ability(data["witness"])
        elif kind == "ManaAbilityAction":
            result = -0.5, True, ("prefer_payment_plan_to_floating_mana",)
        elif kind in {"DeclareAttackersOption", "DeclareBlockersOption"}:
            result = self.combat(data, kind == "DeclareAttackersOption")
        elif kind == 'CombatNextOption':
            context = self.view['decision_context']
            selected = [*context['prefix'], data['target']]
            declarations = {'mapping': [[c, t] for c, t in zip(context['creatures'], selected) if t is not None]}
            result = self.combat({'declarations': declarations}, context['combat_kind'] == 'attackers')
        elif kind == "CombatDamageOption":
            result = self.combat_damage(data)
        elif kind in {"DiscardOption", "MulliganBottomOption"}:
            result = -sum(self.value(self.card(c)) for c in data["cards"]), True, ("retain_material",)
        elif kind == "MulliganOption":
            lands = sum("LAND" in c.get("types", ()) for c in self.view.get("hand", ()))
            prefer = len(self.view.get("hand", ())) >= 6 and (lands == 0 or lands >= 6)
            result = float(data["take_mulligan"] == prefer), True, ("opening_mana_balance",)
        else:
            result = -0.25, False, ("canonical_fallback",)
        return ActionRating(action, *result)

    def combat_damage(self, data):
        """!
        @brief Value a legal split from public characteristics, not card identity.

        This is a local estimate: prevention/replacement/trigger interactions
        remain the engine's job in search. In particular, assigning extra damage
        to an already lethally damaged creature gains no extra material value.
        """
        source = self.card(self.view.get("decision_context", {}).get("creature"))
        keywords = source.get("keywords", ())
        value = 0.0
        for target, amount in _pairs(data.get("assignments")):
            card = self.card(target)
            if card and amount > 0 and "deathtouch" in keywords:
                lethal = "indestructible" not in card.get("keywords", ())
                value -= self.sign(card.get("controller")) * (self.value(card) if lethal else .05 * amount)
            else:
                value += self.damage(target, amount)
        # Infect replaces player life loss; do not mistake it for normal lethal.
        if "infect" in keywords:
            for target, amount in _pairs(data.get("assignments")):
                if "player" in target:
                    value -= self.damage(target, amount)
                    player = self.players.get(target["player"], {})
                    value -= self.sign(target["player"]) * (1000 if amount + player.get("poison_counters", 0) >= 10 else .2 * amount)
        # Break equal material-value splits by the native deterministic split:
        # lethal to earlier recipients, remainder to the last. Recipients are
        # explicit public request context, not option enumeration or card names.
        context = self.view.get("decision_context", {})
        recipients, remaining = context.get("recipients", ()), context.get("amount")
        expected = []
        if recipients and type(remaining) is int:
            for index, target in enumerate(recipients):
                if index == len(recipients) - 1:
                    amount = remaining
                else:
                    card = self.card(target)
                    if not card or card.get("toughness") is None:
                        break
                    lethal = max(0, card["toughness"] - card.get("damage_marked", 0))
                    if "deathtouch" in keywords:
                        lethal = min(lethal, 1)
                    amount = min(remaining, lethal)
                if amount:
                    expected.append((target.get("card"), target.get("player"), amount))
                    remaining -= amount
            else:
                actual = [(target.get("card"), target.get("player"), amount)
                          for target, amount in _pairs(data.get("assignments")) if amount]
                if set(actual) == set(expected):
                    value += 1e-6
        return value, True, ("approximate_combat_damage",)
