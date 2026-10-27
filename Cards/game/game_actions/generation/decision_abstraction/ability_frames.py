"""Lazy ability construction over the shared plan compiler and validators.

Frames are branch-local generation recipes, never executable actions or portable
search identities. Every offered prefix has a complete legal witness. Custom
pipelines/pruning keep their exhaustive protocol rather than changing legality.
"""
from collections import Counter
from dataclasses import dataclass, field, replace

from ...data_structs.game_action import GameAction
from ....target.target_resolver import TargetBinding, TargetOption, TargetResolver
from ....target.target_selector import MinMaxTegetSelector, SingleTargetSelector
from ..ability_action_gen_pipeline import ABILITY_GENERATION_PIPELINE
from ..plan_validation import EXECUTION_PLAN_VALIDATOR
from ..target_binding_generator import FullTargetBindingGenerator, ProvidedTargetBindingGenerator
from ..action_node_option_generator import FullActionNodeOptionGenerator, SelectedActionNodeOptionGenerator
from .requests import AbilityDecisionRequest
from .parameters import ability_parameters


def supports_construction(policy, ability=None):
    from ..exec_plan_gen_pipeline import ExecutionPlanPipeline
    from ..subability_generator import FullSubAbilityGenerator, FixedSubAbilityGenerator
    from ...data_structs.ability import Ability
    if ability is not None and type(ability).generate_actions is not Ability.generate_actions:
        return False
    strategy = policy.strategy
    return (strategy is not None and policy.pruning is None
        and strategy.cost_plan_pruning is None and strategy.action_plan_pruning is None
        and type(strategy.subability_gen) in (FullSubAbilityGenerator, FixedSubAbilityGenerator)
        and all(type(pipeline) is ExecutionPlanPipeline for pipeline in
                (strategy.cost_pipeline, strategy.action_pipeline))
        and all(type(side.target_gen) in (FullTargetBindingGenerator, ProvidedTargetBindingGenerator)
                and type(side.options_gen) in (FullActionNodeOptionGenerator, SelectedActionNodeOptionGenerator)
                for side in (strategy.cost_strategy, strategy.action_strategy)))


def first_closed(values):
    stream = iter(values)
    try:
        return next(stream, None)
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


def context_key(ctx):
    return (ctx.x_value, ctx.life_payment,
            tuple(sorted((k.name, v) for k, v in (ctx.subability_gen_res.selected_variables or {}).items())))


@dataclass(frozen=True)
class Completion:
    context: object
    cost_template: object
    effect_template: object
    cost: object
    effect: object

    def action(self):
        return self.context.ability.to_game_action(self.cost, self.effect)


@dataclass(frozen=True)
class AbilityConstructionOption(GameAction):
    stage: str
    choice: object
    witness: GameAction
    _next_frame: object = field(repr=False, compare=False)

    def get_intents(self):
        raise ValueError("An ability construction prefix must be completed before execution")


@dataclass(frozen=True)
class AbilityConstructionRequest(AbilityDecisionRequest):
    _frame: object = field(default=None, repr=False, compare=False)


class AbilityConstructionPipeline:
    def generate(self, request, policy):
        if policy.pruning is not None:
            raise ValueError("Construction prefixes cannot use complete-action pruning")
        if request._frame is None or request._frame.ability is not request.ability:
            raise ValueError("Construction request needs its own ability frame")
        if request.ability.controller is not request.player:
            raise ValueError("Construction ability must belong to the acting player")
        yield from request._frame.options(request.state)


@dataclass(frozen=True)
class AbilityConstructionFrame:
    ability: object
    policy: object = field(repr=False, compare=False)
    stage: str = "parameters"
    context: object = field(default=None, repr=False, compare=False)
    cost_template: object = field(default=None, repr=False, compare=False)
    effect_template: object = field(default=None, repr=False, compare=False)
    cost_binding: object = field(default=None, repr=False, compare=False)
    effect_binding: object = field(default=None, repr=False, compare=False)
    group_prefix: object = field(default=None, repr=False, compare=False)
    proof: object = field(default=None, repr=False, compare=False)
    cost_mode_prefix: tuple = ()
    effect_mode_prefix: tuple = ()

    def contexts(self, state):
        if self.context is not None:
            yield self.context
            return
        for selected in ability_parameters(self.ability, self.policy, state):
            for ctx in ABILITY_GENERATION_PIPELINE.contexts(self.ability, self.policy.strategy, state,
                    x_value=selected.x_value, life_payment=selected.life_payment):
                # The compiler reuses a mutable context. No suspended iterator
                # or composer is retained in a frame offered to a controller.
                yield replace(ctx)

    def templates(self, ctx, state, side):
        selected = getattr(self, side + "_template")
        if selected is not None:
            yield selected
            return
        strategy = self.policy.strategy
        pipeline = strategy.cost_pipeline if side == "cost" else strategy.action_pipeline
        generation = strategy.cost_strategy if side == "cost" else strategy.action_strategy
        subability = (ctx.subability_gen_res.cost_subability if side == "cost"
                      else ctx.subability_gen_res.action_subability)
        prefix = getattr(self, side + '_mode_prefix')
        if prefix and subability is not None:
            from .mode_frames import resolve_modes
            subability = replace(subability, action_node=resolve_modes(subability.action_node, prefix)[0])
        reuse = (getattr(self.proof, side + "_template") if self.proof is not None
                 and context_key(ctx) == context_key(self.proof.context) else None)
        yield from pipeline.prepare(ctx, subability, generation, state, reuse=reuse)

    def plans(self, ctx, template, state, side):
        strategy = self.policy.strategy
        generation = strategy.cost_strategy if side == "cost" else strategy.action_strategy
        kwargs = {}
        if type(generation.target_gen) is FullTargetBindingGenerator:
            kwargs["prefix_binding"] = getattr(self, side + "_binding")
            if self.stage == side + "_targets":
                kwargs["group_prefix"] = self.group_prefix
        from ....ai.generation_candidates import target_preferences
        with target_preferences(template.effects):
            bindings = generation.target_gen.generate(ctx, template.used_slots, state, **kwargs)
            yield from EXECUTION_PLAN_VALIDATOR.legal_plans(
                (template.bind(binding) for binding in bindings), self.ability, state, is_cost=side == "cost")

    def completions(self, state):
        for ctx in self.contexts(state):
            for cost_template in self.templates(ctx, state, "cost"):
                for cost in self.plans(ctx, cost_template, state, "cost"):
                    for effect_template in self.templates(ctx, state, "effect"):
                        for effect in self.plans(ctx, effect_template, state, "effect"):
                            yield Completion(ctx, cost_template, effect_template, cost, effect)

    def prove(self, state):
        return first_closed(self.completions(state))

    def candidate(self, state, label, choice, **changes):
        frame = replace(self, **changes)
        proof = frame.prove(state)
        if proof is not None:
            assigned = {(key, runtime) for key, inner in (frame.effect_binding or {}).items() for runtime in inner}
            if (frame.stage == "effect_targets" and frame.group_prefix is None
                    and (type(frame.policy.strategy.action_strategy.target_gen) is ProvidedTargetBindingGenerator
                         or assigned == {(s.slot.key, s.runtime_key) for s in frame.effect_template.used_slots})):
                # The final choice already names a complete legal action. Do
                # not spend another simulator transition confirming that action.
                yield proof.action()
            else:
                yield AbilityConstructionOption(label, choice, proof.action(), replace(frame, proof=proof))

    def options(self, state):
        if self.stage == "parameters":
            seen = set()
            for ctx in self.contexts(state):
                key = context_key(ctx)
                if key not in seen:
                    seen.add(key)
                    yield from self.candidate(state, "parameters", key, context=ctx, stage="cost_mode")
        elif self.stage in ("cost_mode", "effect_mode"):
            side = self.stage.removesuffix("_mode")
            subability = (self.context.subability_gen_res.cost_subability if side == 'cost'
                          else self.context.subability_gen_res.action_subability)
            generation = self.policy.strategy.cost_strategy if side == 'cost' else self.policy.strategy.action_strategy
            if subability is not None and type(generation.options_gen) is FullActionNodeOptionGenerator:
                from .mode_frames import resolve_modes
                prefix = getattr(self, side + '_mode_prefix')
                try:
                    _, pending = resolve_modes(subability.action_node, prefix)
                except TypeError:
                    pending = None
                if pending is not None:
                    path, children = pending
                    seen = set()
                    for index, child in enumerate(children):
                        if child in seen:
                            continue
                        seen.add(child)
                        yield from self.candidate(state, side + '_mode_branch', (path, index),
                            **{side + '_mode_prefix': (*prefix, (path, index))})
                    return
            for template in self.templates(self.context, state, side):
                yield from self.candidate(state, self.stage,
                    (template.effects, template.option.mana_req),
                    **{side + "_template": template, "stage": side + "_targets"})
        else:
            yield from self.target_options(state)

    def target_options(self, state):
        side = self.stage.removesuffix("_targets")
        template = getattr(self, side + "_template")
        binding = getattr(self, side + "_binding") or TargetBinding().to_immutable()
        assigned = {(key, runtime) for key, inner in binding.items() for runtime in inner}
        remaining = [s for s in template.used_slots if (s.slot.key, s.runtime_key) not in assigned]
        generation = (self.policy.strategy.cost_strategy if side == "cost"
                      else self.policy.strategy.action_strategy)
        if not remaining or type(generation.target_gen) is ProvidedTargetBindingGenerator:
            if side == "cost":
                yield from replace(self, stage="effect_mode", group_prefix=None).options(state)
            else:
                proof = self.prove(state)
                if proof is not None:
                    yield proof.action()
            return
        slot = self.group_prefix[0] if self.group_prefix else min(remaining,
            key=lambda s: (s.count_options(self.ability.source, self.ability.controller, state, binding),
                           s.slot.key, s.runtime_key))
        prefix = self.group_prefix[1] if self.group_prefix else ()
        resolver = slot.slot.target_resolver
        reserved = slot.get_reserved_objs(binding)
        selector = resolver.target_selector

        def finish(option):
            updated = binding.to_mutable()
            updated.setdefault(slot.slot.key, {})[slot.runtime_key] = option
            return self.candidate(state, side + "_target", (slot.slot.key, slot.runtime_key, "finish", option),
                **{side + "_binding": updated.to_immutable(), "group_prefix": None})

        # Unknown selectors keep their authored group generator. They still
        # avoid the Cartesian product with the other slots and modes.
        if type(resolver) is not TargetResolver or type(selector) not in (MinMaxTegetSelector, SingleTargetSelector):
            for option in resolver.generate_target_options(self.ability.source, self.ability.controller, state, reserved):
                yield from finish(option)
            return
        group = TargetOption(Counter(prefix))
        if selector.is_valid_option(group):
            yield from finish(group)
        if len(prefix) >= selector.max_target_count:
            return
        candidates = tuple(resolver.candidates(self.ability.source, self.ability.controller, state, reserved))
        start = candidates.index(prefix[-1]) + (not selector.is_target_repeatable) if prefix else 0
        from ....ai.generation_candidates import target_order
        from ....ai.generation_candidates import target_preferences
        with target_preferences(template.effects):
            ordered = target_order(candidates[start:], self.ability.source, self.ability.controller, state, resolver)
        for target in ordered:
            selected = (*prefix, target)
            # At the maximum the group is determined, so expose the final
            # binding directly instead of an extra forced finish choice.
            if len(selected) == selector.max_target_count:
                updated = binding.to_mutable()
                updated.setdefault(slot.slot.key, {})[slot.runtime_key] = TargetOption(Counter(selected))
                changes = {side + "_binding": updated.to_immutable(), "group_prefix": None}
            else:
                changes = {"group_prefix": (slot, selected)}
            yield from self.candidate(state, side + "_target", (slot.slot.key, slot.runtime_key, "add", target), **changes)


def choose_construction(request, frame, decide):
    """Fold only forced prefixes; always expose the final executable choice.

    Streams are closed before a controller is called. The final decision is
    retained so search counts one semantic action even if every prefix is forced.
    """
    while True:
        stream = iter(frame.options(request.state))
        try:
            first, second = next(stream, None), next(stream, None)
        finally:
            stream.close()
        if first is None:
            raise ValueError("A proven ability prefix lost its legal completion")
        if second is None and type(first) is AbilityConstructionOption:
            frame = first._next_frame
            continue
        return decide(AbilityConstructionRequest(request.state, request.player, frame.ability, _frame=frame))
