"""Exclusive, RAM-only scopes gated to one native simulation action interval."""
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from functools import wraps
import gc
import inspect
from time import perf_counter
from unittest.mock import patch


class ExecutionBreakdown:
    """!
    @brief Charge elapsed time only between action submission and next request.
    @var stack Hook frames, including frames entered before measurement was armed.
    @var seconds Exclusive wall time per function; child work is never counted twice.
    @var calls Number of measured entries, including frames active at arm time.
    @var active Whether the native action interval is currently being measured.
    """

    def __init__(self):
        self.stack, self.seconds, self.calls = [], defaultdict(float), defaultdict(int)
        self.categories = {}
        self.active = False
        self.started = self.finished = self.last = None

    def flush(self, now):
        """! @brief Charge the current exclusive owner, or the unhooked remainder. """
        if self.active:
            label = self.stack[-1] if self.stack else 'unattributed'
            self.seconds[label] += now-self.last
            self.last = now

    def enter(self, label):
        self.flush(perf_counter())
        self.stack.append(label)
        if self.active:
            self.calls[label] += 1

    def leave(self, label):
        self.flush(perf_counter())
        assert self.stack.pop() == label

    def arm(self):
        """! @brief Start inside already active controller frames. """
        assert not self.active and self.started is None
        self.started = self.last = perf_counter()
        self.active = True
        for label in self.stack:
            self.calls[label] += 1
        return self.started

    def stop(self):
        """! @brief End before the next request generates its menu or observation. """
        assert self.active
        self.finished = perf_counter()
        self.flush(self.finished)
        self.active = False
        return self.finished

    def summary(self):
        """! @brief Export additive function/category totals, not inclusive call costs. """
        assert self.finished is not None and not self.active
        categories = defaultdict(float)
        for label, seconds in self.seconds.items():
            categories[self.categories.get(label, 'unattributed')] += seconds*1000
        total = (self.finished-self.started)*1000
        assert abs(sum(categories.values())-total) < 0.01
        return dict(total_ms=total, categories_ms=dict(categories),
                    functions={label: dict(exclusive_ms=seconds*1000, calls=self.calls[label])
                               for label, seconds in self.seconds.items()})

    def hook(self, owner, attribute, category):
        """! @brief Preserve instance, static and class method binding semantics. """
        descriptor = inspect.getattr_static(owner, attribute)
        kind = type(descriptor) if isinstance(descriptor, (classmethod, staticmethod)) else None
        original = descriptor.__func__ if kind else getattr(owner, attribute)
        label = f'{original.__module__}.{original.__qualname__}'
        self.categories[label] = category

        @wraps(original)
        def wrapped(*args, **kwargs):
            self.enter(label)
            try:
                return original(*args, **kwargs)
            finally:
                self.leave(label)
        return patch.object(owner, attribute, kind(wrapped) if kind else wrapped)

    def garbage_collection(self, phase, info):
        """! @brief Separate GC pauses instead of blaming the interrupted rule function. """
        label = 'python.garbage_collection'
        self.categories[label] = 'garbage_collection'
        if phase == 'start':
            self.enter(label)
        else:
            self.leave(label)

    @contextmanager
    def installed(self):
        """! @brief Temporarily instrument native components; restore even on failure. """
        from game.ai.search_simulator import _Replay, _ReplayController, SimulationNode
        from game.ai.search_recall import PlayerRecall
        from game.ai.search_descriptors import ChoiceDescriptor
        from game.ai.generation_candidates import CandidateFrontier
        from game.ai.knowledge import PlayerKnowledge
        from game.ai.checkpoint_simulator import _Checkpoint, _CheckpointReplay
        from game.game_loop.game_loop import PriorityWindow
        from game.rules.combat import CombatState
        from game.game_state.state import State
        from game.game_state.registers.indexed_register import IndexedRegister
        from game.game_actions.resolution.action_processor import ActionProcessor
        from game.game_actions.resolution.resolution_engine import ResolutionEngine
        from game.game_actions.resolution.operation_executor import OperationExecutor
        from game.game_actions.resolution.cost_transaction import CostTransaction
        from game.game_actions.resolution.validator import TargetValidator
        from game.game_actions.resolution.sba_resolver import SBAResolver
        from game.game_actions.resolution.replacement_effects import ReplacementResolver
        from game.game_actions.resolution import event_bus, operation_executor
        from game.game_actions.triggers import runtime_triggers
        hooks = [
            (SimulationNode, 'bind_action', 'action_binding'),
            (_Replay, '_select', 'action_binding'),
            (ChoiceDescriptor, 'from_option', 'action_descriptors'),
            (PlayerRecall, 'append', 'recall'),
            (CandidateFrontier, 'close', 'frontier_cleanup'),
            (ActionProcessor, 'process', 'action_processing'),
            (TargetValidator, 'validate', 'validation'),
            (OperationExecutor, 'execute_batch', 'operation_batch'),
            (OperationExecutor, 'execute_operation', 'operation_execution'),
            (SBAResolver, 'resolve', 'state_based_actions'),
            (State, 'synchronise_registers', 'state_sync'),
            (State, 'refresh_continuous_effects', 'continuous_effects'),
            (IndexedRegister, 'synchronise', 'index_sync'),
            (ReplacementResolver, 'replace', 'replacement_effects'),
            (event_bus.EventBus, 'emit', 'event_dispatch'),
            (event_bus.TriggerProcessor, 'process', 'trigger_processing'),
            (event_bus.EventBus, 'collect_trigger_abilities', 'trigger_matching'),
            (runtime_triggers, 'collect_runtime', 'trigger_matching'),
            (_ReplayController, 'receive_player_event', 'player_events'),
            (PlayerKnowledge, 'observe', 'player_knowledge'),
            (_CheckpointReplay, 'decide', 'checkpoint_adapter'),
            (_Checkpoint, 'capture', 'checkpoint_capture'),
            (_Checkpoint, 'freeze', 'checkpoint_copy'),
            (CombatState, 'declare_attackers', 'combat_declaration'),
            (CombatState, 'declare_blockers', 'combat_declaration'),
        ]
        hooks.extend((CostTransaction, name, 'cost_transaction') for name in (
            '__init__', 'close', 'watch_operations', 'rollback'))
        hooks.extend((ResolutionEngine, name, category) for name, category in (
            ('_resolve', 'resolution_planning'), ('resolve_simultaneous', 'resolution_planning'),
            ('_validate', 'validation'), ('_apply_replacement_effects', 'replacement_effects'),
            ('_resolve_state', 'state_based_actions'), ('_emit_events', 'event_dispatch'),
            ('_process_triggers', 'trigger_processing'), ('settle', 'settle_other')))
        hooks.extend((PriorityWindow, name, 'priority_flow') for name in (
            '_get_player_action', '_process_action', '_pass', '_advance_priority',
            '_sync_priority', '_resolve_top_stack'))
        # OperationExecutor imports these helpers by value; cover both aliases.
        hooks.extend((module, name, category) for module in (event_bus, operation_executor)
                     for name, category in (
                         ('capture_trigger_roster', 'trigger_matching'),
                         ('capture_card_information', 'last_known_information'),
                         ('capture_player_events', 'player_event_capture'),
                         ('capture_event', 'event_capture')))
        with ExitStack() as stack:
            for owner, attribute, category in hooks:
                stack.enter_context(self.hook(owner, attribute, category))
            gc.callbacks.append(self.garbage_collection)
            try:
                yield self
            finally:
                if self.active:
                    self.stop()
                gc.callbacks.remove(self.garbage_collection)
        assert not self.stack
