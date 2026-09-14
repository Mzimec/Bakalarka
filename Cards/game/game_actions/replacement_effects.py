"""Install resolved replacement effects through the ordinary effect graph."""

from .data_structs.effect import Effect
from .data_structs.operation import Operation
from .resolution.event_bus import GameEvent


class InstallReplacementOperation(Operation):
    """!
    @brief Install a bound replacement effect into the game state.

    By default the installed effect is independent of the source that created
    it. Anchored effects are instead bound to the source incarnation and use
    the replacement system's normal source-lifetime checks.
    """

    def __init__(self, context, definition, *, anchored=False):
        """!
        @brief Create an operation that installs a replacement definition.

        @param context Bound resolution context.
        @param definition Replacement-effect definition to instantiate.
        @param anchored Whether to bind the runtime effect to the source.
        """
        super().__init__(context)
        self.definition = definition
        self.anchored = anchored

    def execute(self, state):
        """!
        @brief Bind and register the replacement effect.

        @param state Current game state.
        @return A single `replacement_installed` event.
        """
        # Passing no source creates an independent resolved effect. Anchored
        # effects retain the source-incarnation lifetime semantics implemented
        # by ReplacementEffect.
        effect = self.definition.bind(
            self.context.source if self.anchored else None
        )

        state.replacement_rules.append(effect)

        return [
            GameEvent(
                "replacement_installed",
                self.context.source,
                self.context.controller,
                {"replacement": effect},
            )
        ]


class InstallReplacementEffect(Effect):
    """!
    @brief Create and install a replacement effect during resolution.

    The factory receives the live state and resolution context, allowing the
    resulting definition to capture choices such as targets, values or duration.

    Ordinary resolved replacement effects are independent of their source's
    later zone changes. Setting `anchored=True` explicitly ties the installed
    effect to that source incarnation.

    @var factory
        Pure factory producing a replacement-effect definition.
    @var anchored
        Whether the installed runtime effect is bound to its source.
    """

    def __init__(self, key, factory, *, anchored=False):
        super().__init__(key)
        self.factory = factory
        self.anchored = anchored

    def to_operations(self, state, context):
        """!
        @brief Materialize and generate the replacement-installation operation.

        @param state Current game state.
        @param context Bound resolution context.
        """
        yield InstallReplacementOperation(
            context,
            self.factory(state, context),
            anchored=self.anchored,
        )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Create a replacement/prevention effect."