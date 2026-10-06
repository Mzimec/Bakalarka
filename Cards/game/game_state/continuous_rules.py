"""Bind static continuous rules to sources and maintain their live query matches."""

from dataclasses import dataclass
from .modifier import (
    ContinuousEffect,
    ContinuousEffectDefinition,
    ContinuousEffectState,
    AnchoredDuration,
    DynamicTargetingStrategy,
)
from ..enums import ZoneType


@dataclass(frozen=True)
class StaticContinuousRule:
    """!
    @brief Declarative static continuous ability attached to a card definition.

    The rule is inactive on its own. `bind()` creates a runtime
    `ContinuousEffect` anchored to a particular source card incarnation.

    @var key
        Stable key identifying this rule within its source.
    @var target_spec
        Dynamic query describing objects affected by the rule.
    @var modifiers
        Stat modifiers contributed by this continuous effect.
    @var depends_on
        Explicit dependencies on other continuous effects.
    @var characteristic_defining
        Whether this rule is a characteristic-defining ability.
    @var active_zones
        Zones in which the source keeps this rule active.
    """

    key: str
    target_spec: object
    modifiers: object
    depends_on: frozenset[str] = frozenset()
    characteristic_defining: bool = False
    active_zones: frozenset = frozenset({ZoneType.BATTLEFIELD})

    def bind(self, source, state):
        """!
        @brief Bind this static rule to one runtime source.

        Relative dependency keys are scoped to the same source. Keys already
        containing `:` are treated as fully-qualified effect keys.

        @param source Runtime card supplying the rule.
        @param state Current game state.
        @return Runtime continuous effect anchored to `source`.
        """
        definition = ContinuousEffectDefinition(
            duration=AnchoredDuration(self.active_zones, source),
            source=source,
            created_at=state.time_stamp,
            targeting=DynamicTargetingStrategy(self.target_spec),
            modifiers=self.modifiers,
            depends_on=frozenset(
                key if ":" in key else f"{source.key}:{key}" for key in self.depends_on
            ),
            static_ability_key=self.key,
            characteristic_defining=self.characteristic_defining,
        )
        return ContinuousEffect(
            f"{source.key}:{self.key}", definition, ContinuousEffectState(set())
        )


def refresh_continuous_effects(state):
    """!
    @brief Rebuild live continuous-effect matches and apply them layer by layer.

    Static rules are first synchronized with their source cards. Expired effects
    are removed, existing registrations are detached, and the remaining effects
    are then evaluated in layer order.

    The card query indexes are synchronized between effect applications because
    later dynamic target queries may depend on characteristics modified by
    earlier effects.

    @param state Game state whose continuous effects should be refreshed.
    """
    # Index synchronization may itself request an effect refresh. Avoid
    # recursively rebuilding the same effect graph while a refresh is active.
    if state._refreshing_effects:
        return

    # Within one timestamp, a clean effect graph can be reused unchanged.
    if not state._effects_dirty and state._effects_checked_at == state.time_stamp:
        return

    state._refreshing_effects = True
    try:
        manager = state._cont_effect_manager
        desired = {}

        # Discover all static rules whose source currently occupies an active
        # zone. Runtime keys combine source identity with the rule-local key.
        from helper.query_system.query import EqQuery
        from .registers.card_register import IK_STATIC_CONTINUOUS

        for card in state.query_cards(EqQuery(IK_STATIC_CONTINUOUS, True)):
            for rule in card.definition.continuous_effects:
                if card.get_zone() in rule.active_zones:
                    desired[f"{card.key}:{rule.key}"] = (rule, card)

        # Remove static effects whose source/rule is no longer active.
        for key in state._static_effect_keys - desired.keys():
            effect = manager.get(key)
            if effect:
                effect.detach(state)
                manager.pop(key)

        # Bind newly active static rules. Existing runtime effects are retained
        # so their runtime state is not unnecessarily recreated.
        for key, (rule, card) in desired.items():
            if manager.get(key) is None:
                manager.add(rule.bind(card, state))

        state._static_effect_keys = set(desired)

        # Non-static/resolved effects may also expire independently of the
        # source-zone synchronization above.
        for effect in manager.register.expiration_candidates(state):
            if effect.is_over(state):
                effect.detach(state)
                manager.pop(effect.key)

        effects = manager.query()

        if not effects:
            state._continuous_application_signature = None
            state._continuous_graph_signature = None
            state._effects_dirty = False
            state._effects_checked_at = state.time_stamp
            return

        from ..stat_type import STAT_ATTACH_MODS
        # Changing the modifiers granted by an attachment can alter its host
        # without changing the host's own source memberships.
        dynamic_attachments = any(STAT_ATTACH_MODS in effect.modifiers for effect in effects)
        # Unmodified invariant cards need no repeated validation per layer.
        # Keep every initially dynamic/dirty card, even if detaching effects
        # makes it temporarily base-only. Each newly affected target joins too.
        register = state.card_register
        layer_cards = {
            card: register.layer_index_keys(card, force_general=dynamic_attachments)
            for card in register.layer_candidates.select()
        }

        from .continuous_dependencies import application_signature, graph_signature
        signature = application_signature(state, effects)
        if (signature is not None
                and signature == getattr(state, "_continuous_application_signature", None)
                and graph_signature(effects) == getattr(state, "_continuous_graph_signature", None)):
            # Targets, modifier values and ordering are unchanged. Still update
            # every candidate: custom characteristics may read live runtime data.
            for card, indexes in layer_cards.items():
                register.mark_layer_changed(card, indexes)
            register.synchronise()
            state._continuous_graph_reuses = getattr(state, "_continuous_graph_reuses", 0) + 1
            state._effects_dirty = False
            state._effects_checked_at = state.time_stamp
            return

        state._continuous_application_signature = None
        state._continuous_graph_signature = None
        state._continuous_graph_rebuilds = getattr(state, "_continuous_graph_rebuilds", 0) + 1

        # Start from a clean application graph. Target registrations and
        # inferred layer dependencies are rebuilt from the current game state.
        for effect in effects:
            effect.detach(state)
            effect.state.active_layers = set()
            effect.state.inferred_dependencies = {}

        # One continuous effect can contribute modifiers in several layers, so
        # build a layer -> effects mapping before performing ordered evaluation.
        from .registers.effect_register import IK_EFFECT_LAYER
        from ..enums import Layer

        layers = {
            layer: matching for layer in Layer
            if (matching := manager.query(EqQuery(IK_EFFECT_LAYER, layer)))
        }

        started = set()

        for layer in sorted(layers, key=lambda value: value.value):
            # Characteristic reads during dependency analysis/evaluation are
            # limited to effects up through the current layer.
            state._layer_ceiling = layer

            # Earlier-layer modifications may have changed queryable card
            # characteristics. Refresh the shared indexes before evaluating
            # target queries and dependencies for this layer.
            for card, indexes in layer_cards.items():
                register.mark_layer_changed(card, indexes)
            state.card_register.synchronise()
            register._layer_read_cards.clear()

            from .layer_dependencies import order_layer

            for effect in order_layer(state, layers[layer], layer, started):
                # Dynamic targets are selected when the effect first becomes
                # active in the layer pipeline. Later layers of the same effect
                # reuse that registered affected set.
                if effect.key not in started:
                    matching = tuple(effect.get_affected(state))
                    for card in matching:
                        effect._register(card)
                    started.add(effect.key)

                effect.state.active_layers.add(layer)

                # Applying this layer can change values exposed through the card
                # register, which subsequent effects in the same/later layers
                # may query.
                for card in effect.state.currently_affected:
                    # Retain keys written by detached/earlier effects too.
                    indexes = layer_cards.get(card, frozenset()) | register.layer_index_keys(
                        card, force_general=dynamic_attachments)
                    layer_cards[card] = indexes
                    register.mark_layer_changed(card, indexes)
                state.card_register.synchronise()
                # Dependency probes may temporarily add an unknown modifier.
                register._layer_read_cards.clear()

        # Remove the temporary layer ceiling and expose the final fully-layered
        # characteristics through the query indexes.
        state._layer_ceiling = None

        for card, indexes in layer_cards.items():
            register.mark_layer_changed(card, indexes)
        state.card_register.synchronise()

        state._continuous_application_signature = signature
        state._continuous_graph_signature = graph_signature(effects) if signature is not None else None
        state._effects_dirty = False
        state._effects_checked_at = state.time_stamp

    finally:
        # Always release both guards even if dependency ordering, targeting or
        # index synchronization raises.
        state._layer_ceiling = None
        state._refreshing_effects = False
        state.card_register._layer_read_cards.clear()
