"""Combat declarations and damage assignment (CR 506–510 and 702).

Declarations are validated in full before changing any permanent. Damage is
assigned before any of it is dealt, then resolved through the ordinary operation
pipeline so replacement effects, triggered abilities, and state-based actions
have the same semantics as spell damage. Damage assignment follows the current
rules: blockers have no damage assignment order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from game.enums import CardType, TurnPhase, ZoneType
from helper.query_system.query import EqQuery
from game.game_state.registers.card_register import IK_ZONE, IK_TYPE, IK_CONTROLLER, IK_TAPPED
from game.game_actions.resolution.event_bus import GameEvent

if TYPE_CHECKING:
    from game.game_state import Card, Player, State


class CombatError(ValueError):
    """!
    @brief An illegal declaration or combat damage assignment.
    """


class CombatState:
    """!
    @brief Mutable state and validation logic for one combat phase.

    Tracks attackers, blockers, remembered object identities, blocked status,
    and combat-damage-step bookkeeping. Participants are identified by both
    runtime object and zone incarnation so leaving and re-entering combat does
    not preserve participation.
    """

    def __init__(self, state: State):
        """!
        @brief Create an inactive combat state bound to a game state.

        @param state Owning game state.
        """
        self.state = state
        self.end()

    def end(self):
        """!
        @brief Reset all state associated with the current combat.
        """
        self.active = False
        self.attackers: dict[Card, Player] = {}
        self.blockers: dict[Card, Card] = {}
        self.blocked: set[Card] = set()
        self._identities = {}
        self._defenders = {}
        self._removed_defenders = set()
        self._attackers_declared = False
        self.had_attackers = False
        self._blockers_declared = set()
        self._first_damage_done = False
        self._regular_damage_done = False
        self._first_strike_step = False
        self._normal_damage_creatures = set()

    def begin(self):
        """!
        @brief Start a fresh combat phase.
        """
        self.end()
        self.active = True

    def _keyword(self, creature, keyword):
        """!
        @brief Test a combat-relevant keyword using current characteristics.
        """
        return creature.has_keyword(self.state, keyword)

    def _creature(self, card, controller):
        """!
        @brief Check that a card is still the expected controlled battlefield creature.
        """
        return (
            getattr(card, "_game_state", None) is self.state
            and card.get_zone() == ZoneType.BATTLEFIELD
            and card.is_type(self.state, CardType.CREATURE)
            and card.get_controller(self.state) is controller
        )

    def _remember(self, card):
        """!
        @brief Snapshot a combat participant's incarnation and controller.
        """
        self._identities[card] = (card.zone_revision, card.get_controller(self.state))

    def prune(self):
        """!
        @brief Remove participants that changed zone, controller, or creature type.

        A participant that leaves and returns is a new incarnation and therefore
        no longer participates in this combat.
        """
        for defender in self._defenders:
            if not self._defender_present(defender):
                self._removed_defenders.add(defender)

        for card in tuple(self.attackers) + tuple(self.blockers):
            revision, controller = self._identities[card]
            if card.zone_revision != revision or not self._creature(card, controller):
                self.remove(card)

    def remove(self, card):
        """!
        @brief Remove an object from combat, preserving an attacker's blocked status.

        Removing an attacker does not erase the fact that its blocker had been
        declared. Likewise, a previously blocked attacker remains blocked even
        after all blockers leave combat.

        @param card Combat participant or defender to remove.
        """
        if card in self._defenders:
            self._removed_defenders.add(card)

        self.attackers.pop(card, None)
        self.blockers.pop(card, None)

        # A blocker remains a blocking creature if its attacker leaves combat,
        # but has no creature to assign damage to (CR 509.1g, 510.1d).
        self._identities.pop(card, None)

    def legal_attackers(self, player):
        """!
        @brief Return creatures this player may currently declare as attackers.

        @param player Player attempting to attack.
        @return Tuple of legal attacking creatures.
        """
        if player is not self.state.active_player:
            return ()

        return tuple(
            card
            for card in self.state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                & EqQuery(IK_TYPE, CardType.CREATURE) & EqQuery(IK_CONTROLLER, player)
                & EqQuery(IK_TAPPED, False))
            if self._creature(card, player) and self.attacker_error(card, player) is None
        )

    def attacker_error(self, card, player):
        """!
        @brief Return the first reason a creature cannot attack.

        @param card Candidate attacker.
        @param player Declaring player.
        @return Error string, or `None` when the attacker is legal.
        """
        if not self._creature(card, player):
            return "An attacker must be a battlefield creature you control."

        if card.is_tapped:
            return "A tapped creature cannot attack."

        if card.is_summoning_sick(self.state) and not self._keyword(card, "haste"):
            return "A creature with summoning sickness cannot attack."

        if self._keyword(card, "defender") or self._keyword(card, "can't attack"):
            return "This creature cannot attack."

        return None

    def _attacker_mapping(self, player, declarations):
        """!
        @brief Normalize attacker declarations to attacker -> defender mapping.

        Iterable-only declarations are accepted only when there is exactly one
        opposing player, because otherwise the intended defender is ambiguous.

        @param player Attacking player.
        @param declarations Mapping or iterable of attacking creatures.
        @return Normalized declaration mapping.
        @throws CombatError If declarations are ambiguous or contain duplicates.
        """
        if isinstance(declarations, Mapping):
            return dict(declarations)

        creatures = list(declarations)

        if len(set(creatures)) != len(creatures):
            raise CombatError("A creature cannot be declared twice.")

        opponents = [p for p in self.state.active_players if p is not player]

        if len(opponents) != 1 and creatures:
            raise CombatError("Specify the defending player for each attacker.")

        return {card: opponents[0] for card in creatures}

    def validate_attackers(self, player, declarations):
        """!
        @brief Validate a complete attacker declaration without mutating combat state.

        @param player Player declaring attackers.
        @param declarations Proposed attacker declarations.
        @return Normalized attacker -> defender mapping.
        @throws CombatError If any declaration is illegal.
        """
        if not self.active or self.state.turn.phase != TurnPhase.DECLARE_ATTACKERS:
            raise CombatError("Attackers are declared only during the declare attackers step.")

        if player is not self.state.active_player:
            raise CombatError("Only the active player declares attackers.")

        if self._attackers_declared:
            raise CombatError("Attackers have already been declared this combat.")

        proposed = self._attacker_mapping(player, declarations)

        for card, defender in proposed.items():
            error = self.attacker_error(card, player)
            if error:
                raise CombatError(error)

            defending_player = self.defending_player(defender)

            if defending_player is player or defending_player not in self.state.active_players:
                raise CombatError("Attack an opposing player or a planeswalker they control.")

        return proposed

    def declare_attackers(self, player, declarations: Mapping | Iterable = ()):
        """!
        @brief Commit a previously validated attacker declaration.

        Non-vigilance attackers are tapped only after the entire declaration has
        passed validation.

        @param player Active player declaring attackers.
        @param declarations Proposed declarations.
        @return Events generated by attacker declaration.
        """
        proposed = self.validate_attackers(player, declarations)
        self.attackers = proposed
        self.had_attackers = bool(proposed)
        self._attackers_declared = True
        events = []

        for attacker, defender in proposed.items():
            self._remember(attacker)

            if defender not in self.state.players:
                # Preserve the planeswalker's declared incarnation/controller
                # even if its current characteristics later change.
                self._defenders[defender] = (
                    defender.zone_revision,
                    defender.get_controller(self.state),
                )

            if not self._keyword(attacker, "vigilance"):
                attacker.is_tapped = True
                events.append(GameEvent("card_tapped", attacker, player))

            events.append(
                GameEvent(
                    "attacker_declared",
                    attacker,
                    player,
                    {"defender": defender, "attacker": attacker},
                )
            )

        events.append(
            GameEvent(
                "attackers_declared", controller=player, payload={"attackers": tuple(proposed)}
            )
        )
        return events

    def blocker_error(self, blocker, attacker, player):
        """!
        @brief Return the first reason a proposed block is illegal.

        @param blocker Candidate blocking creature.
        @param attacker Creature it would block.
        @param player Defending player.
        @return Error string, or `None` when the block is legal.
        """
        if not self._creature(blocker, player):
            return "A blocker must be a battlefield creature you control."

        if blocker.is_tapped or self._keyword(blocker, "can't block"):
            return "This creature cannot block."

        if (
            attacker not in self.attackers
            or self.defending_player(self.attackers[attacker], declared=True) is not player
        ):
            return "A creature can block only a creature attacking its controller."

        if self._keyword(attacker, "unblockable") or self._keyword(attacker, "can't be blocked"):
            return "This attacker cannot be blocked."

        if self._keyword(attacker, "flying") and not (
            self._keyword(blocker, "flying") or self._keyword(blocker, "reach")
        ):
            return "A flying attacker needs a blocker with flying or reach."

        return None

    def legal_blockers(self, player, attacker):
        """!
        @brief Return creatures that may currently block one attacker.

        @param player Defending player.
        @param attacker Attacker to test.
        @return Tuple of legal blockers.
        """
        self.prune()

        return tuple(
            card
            for card in self.state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                & EqQuery(IK_TYPE, CardType.CREATURE) & EqQuery(IK_CONTROLLER, player)
                & EqQuery(IK_TAPPED, False))
            if self.blocker_error(card, attacker, player) is None
        )

    def validate_blockers(self, player, declarations):
        """!
        @brief Validate a complete blocker declaration without mutating combat state.

        Includes ordinary legality, menace, and the engine's supported
        "must be blocked by all" requirement.

        @param player Defending player.
        @param declarations Mapping or iterable of `(blocker, attacker)` pairs.
        @return Normalized blocker -> attacker mapping.
        @throws CombatError If any declaration or blocking requirement is illegal.
        """
        if not self.active or self.state.turn.phase != TurnPhase.DECLARE_BLOCKERS:
            raise CombatError("Blockers are declared only during the declare blockers step.")

        if not self._attackers_declared:
            raise CombatError("Declare attackers before declaring blockers.")

        if player is self.state.active_player or player not in self.state.active_players:
            raise CombatError("Only a defending player declares blockers.")

        if player in self._blockers_declared:
            raise CombatError("This player's blockers have already been declared.")

        self.prune()

        if not isinstance(declarations, Mapping):
            pairs = list(declarations)

            if len({blocker for blocker, _ in pairs}) != len(pairs):
                raise CombatError("A creature cannot block multiple attackers.")

            declarations = dict(pairs)

        proposed = dict(declarations)
        counts = {}

        for blocker, attacker in proposed.items():
            error = self.blocker_error(blocker, attacker, player)
            if error:
                raise CombatError(error)

            counts[attacker] = counts.get(attacker, 0) + 1

        for attacker, count in counts.items():
            if count == 1 and self._keyword(attacker, "menace"):
                raise CombatError("Menace requires at least two blockers.")

        forced = [
            attacker
            for attacker in self.attackers
            if self._keyword(attacker, "must be blocked by all")
        ]

        for attacker in forced:
            legal = self.legal_blockers(player, attacker)

            # An impossible menace block does not create an impossible forced
            # blocking requirement.
            if self._keyword(attacker, "menace") and len(legal) < 2:
                continue

            for blocker in legal:
                if proposed.get(blocker) not in forced:
                    raise CombatError(
                        "Each able creature must block a creature requiring it to block."
                    )

        return proposed

    def declare_blockers(self, player, declarations=()):
        """!
        @brief Commit one defending player's complete blocker declaration.

        @param player Defending player.
        @param declarations Proposed blocks.
        @return Events generated by blocker declaration.
        """
        proposed = self.validate_blockers(player, declarations)
        self._blockers_declared.add(player)
        self.blockers.update(proposed)
        events = []

        # `blocked` is historical for the combat: once blocked, an attacker
        # remains blocked even if its blockers later leave combat.
        newly_blocked = set(proposed.values()) - self.blocked
        self.blocked.update(proposed.values())

        for blocker, attacker in proposed.items():
            self._remember(blocker)
            events.append(
                GameEvent(
                    "blocker_declared", blocker, player, {"attacker": attacker, "blocker": blocker}
                )
            )

        for attacker in self.attackers:
            if attacker in newly_blocked:
                events.append(
                    GameEvent(
                        "attacker_blocked",
                        attacker,
                        attacker.get_controller(self.state),
                        {"blockers": tuple(b for b, a in proposed.items() if a is attacker)},
                    )
                )

        events.append(
            GameEvent(
                "blockers_declared",
                controller=player,
                payload={"blockers": tuple(proposed.items())},
            )
        )
        return events

    def has_first_strike_damage(self):
        """!
        @brief Return whether this combat needs a first-strike damage step.
        """
        self.prune()

        return any(
            self._keyword(card, "first strike") or self._keyword(card, "double strike")
            for card in tuple(self.attackers) + tuple(self.blockers)
        )

    def defending_player(self, defender, *, declared=False):
        """!
        @brief Resolve the player represented by a combat defender.

        A defender may be a player directly or a planeswalker controlled by one.
        When `declared` is true, a remembered planeswalker controller is used so
        blocking legality refers to the original attack declaration.

        @param defender Player or planeswalker being attacked.
        @param declared Whether declared combat identity should be consulted.
        @return Defending player, or `None` if the defender is no longer valid.
        """
        if defender in self.state.players:
            return defender

        if declared and defender in self._defenders:
            return self._defenders[defender][1]

        if (
            getattr(defender, "_game_state", None) is self.state
            and defender.get_zone() == ZoneType.BATTLEFIELD
            and CardType.PLANESWALKER in defender.get_types(self.state)
        ):
            return defender.get_controller(self.state)

        return None

    def _defender_present(self, defender):
        """!
        @brief Check that a declared defender still represents the same game object.
        """
        if defender in self.state.players:
            return defender in self.state.active_players

        if defender in self._removed_defenders:
            return False

        identity = self._defenders.get(defender)

        return (
            identity is not None
            and defender.zone_revision == identity[0]
            and self.defending_player(defender) is identity[1]
        )

    def _recipients(self, creature):
        """!
        @brief Return legal combat-damage recipients for one dealing creature.

        Attackers deal to blockers unless unblocked or trampling. Blockers deal
        only to their still-participating attacker.
        """
        if creature in self.attackers:
            blockers = tuple(b for b, a in self.blockers.items() if a is creature)
            defender = self.attackers[creature]
            recipient = (defender,) if self._defender_present(defender) else ()

            if creature not in self.blocked:
                return recipient

            if self._keyword(creature, "trample"):
                return blockers + recipient

            return blockers

        attacker = self.blockers[creature]
        return (attacker,) if attacker in self.attackers else ()

    def _lethal(self, source, target):
        """!
        @brief Compute lethal combat damage still required for one creature.

        Deathtouch reduces lethal assignment to one damage when positive
        toughness remains.

        @param source Damage-dealing creature.
        @param target Blocking or blocked creature.
        @return Amount considered lethal for assignment purposes.
        """
        remaining = max(0, (target.get_toughness(self.state) or 0) - target.state.damage_marked)
        return min(remaining, 1) if self._keyword(source, "deathtouch") else remaining

    def default_assignment(self, creature, recipients, amount):
        """!
        @brief Produce a deterministic legal combat-damage split.

        Earlier recipients receive lethal damage and the final recipient gets
        the remainder. Controllers may provide any other legal split.

        @param creature Damage-dealing creature.
        @param recipients Ordered legal recipients.
        @param amount Total combat damage to assign.
        @return Mapping from recipients to assigned damage.
        """
        assigned = {}
        remaining = amount

        for index, recipient in enumerate(recipients):
            if index == len(recipients) - 1:
                damage = remaining
            else:
                damage = min(remaining, self._lethal(creature, recipient))

            if damage:
                assigned[recipient] = damage
                remaining -= damage

        return assigned

    def _validate_assignment(self, creature, recipients, amount, assigned):
        """!
        @brief Validate one creature's complete combat-damage assignment.

        @throws CombatError If recipients, amounts, total damage, or trample
            lethal-assignment requirements are invalid.
        """
        if not isinstance(assigned, Mapping):
            raise CombatError("Damage assignments must map recipients to amounts.")

        if any(
            target not in recipients or type(damage) is not int or damage < 0
            for target, damage in assigned.items()
        ):
            raise CombatError("Damage needs legal recipients and nonnegative integer amounts.")

        if sum(assigned.values()) != (amount if recipients else 0):
            raise CombatError("Assign all of the creature's combat damage.")

        if creature in self.attackers and self._keyword(creature, "trample"):
            defender = self.attackers[creature]

            if assigned.get(defender, 0):
                blockers = [b for b, a in self.blockers.items() if a is creature]

                if any(
                    assigned.get(blocker, 0) < self._lethal(creature, blocker)
                    for blocker in blockers
                ):
                    raise CombatError(
                        "Trample needs lethal damage assigned to every blocker first."
                    )

    def damage_operations(self, *, first_strike=False, assignments=None):
        """!
        @brief Build one simultaneous combat-damage operation group.

        `assignments` maps each dealing creature to `{recipient: damage}`.
        Missing assignments are delegated to the controller's optional
        `CombatDamageRequest` when a choice exists, otherwise a
        deterministic legal assignment is used.

        Every assignment is validated before any damage operation is returned
        or damage-step bookkeeping is committed. The resulting operations are
        intended to pass through the ordinary simultaneous resolution pipeline.

        @param first_strike Whether this is the first-strike damage step.
        @param assignments Optional explicit damage assignments.
        @return Tuple of damage operations to resolve simultaneously.
        @throws CombatError If the damage step or any assignment is illegal.
        """
        if not self.active:
            raise CombatError("There is no active combat.")

        expected = TurnPhase.FIRST_COMBAT_DAMAGE if first_strike else TurnPhase.SECOND_COMBAT_DAMAGE

        if self.state.turn.phase != expected:
            raise CombatError("Combat damage can be dealt only in its damage step.")

        if not self._attackers_declared:
            raise CombatError("Declare attackers before assigning damage.")

        if self._regular_damage_done or (first_strike and self._first_damage_done):
            raise CombatError("Combat damage has already been assigned in this step.")

        self.prune()

        creatures = tuple(self.attackers) + tuple(self.blockers)

        early = {
            c
            for c in creatures
            if self._keyword(c, "first strike") or self._keyword(c, "double strike")
        }

        if not first_strike and not self._first_damage_done and early:
            raise CombatError("Resolve first strike combat damage before regular damage.")

        if first_strike:
            eligible = early

        elif self._first_strike_step:
            # Creatures that did not deal damage in the first-strike step deal
            # now, together with surviving double-strike creatures.
            eligible = self._normal_damage_creatures | {
                c for c in creatures if self._keyword(c, "double strike")
            }

        else:
            eligible = set(creatures)

        assignments = assignments if assignments is not None else {}

        if not isinstance(assignments, Mapping) or any(c not in eligible for c in assignments):
            raise CombatError("Only creatures dealing damage this step can assign it.")

        chosen = []

        # Collect and validate every assignment before constructing operations.
        # This preserves the simultaneous nature of combat damage.
        for creature in creatures:
            if creature not in eligible:
                continue

            amount = max(0, creature.get_power(self.state) or 0)
            recipients = self._recipients(creature)
            assigned = assignments.get(creature)

            if assigned is None:
                controller = creature.get_controller(self.state)
                if len(recipients) > 1 and amount:
                    from ..ai.decision_maker import CombatDamageRequest
                    assigned = controller.decision_maker.decide(CombatDamageRequest(
                        self.state, controller, creature, tuple(recipients), amount,
                    )).value.assignments
                else:
                    assigned = self.default_assignment(creature, recipients, amount)

            self._validate_assignment(creature, recipients, amount, assigned)

            chosen.extend(
                (creature, target, damage) for target, damage in assigned.items() if damage
            )

        from game.game_actions.data_structs.game_action import ResolutionContext
        from game.operations.card_operations import DamageCreatureOperation, DamagePlayerOperation

        operations = []

        for creature, target, damage in chosen:
            context = ResolutionContext(
                controller=creature.get_controller(self.state),
                source=creature,
                action_key="combat_damage",
            )

            operation_type = (
                DamagePlayerOperation if target in self.state.players else DamageCreatureOperation
            )

            operation = operation_type(context, target, damage)
            operation.combat = True
            operations.append(operation)

        # Commit damage-step bookkeeping only after every assignment has passed
        # validation and the complete simultaneous operation batch exists.
        if first_strike:
            self._first_damage_done = True
            self._first_strike_step = bool(early)
            self._normal_damage_creatures = set(creatures) - early
        else:
            self._regular_damage_done = True

        return tuple(operations)