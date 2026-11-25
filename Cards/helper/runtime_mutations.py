"""Loan-local first-write records and revision tokens for owned runtime data.

No engine or AI imports. An explicit owner supplies reviewed records and binds a
broker for a synchronous loan. Nothing is enabled by importing this module.
Root tokens are reusable only after the owner has restored its complete root;
branch tokens come from a clock that never rolls back (no sibling ABA reuse).
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections import OrderedDict, deque


_active = ContextVar('runtime_mutations', default=None)


class MutationBroker:
    def __init__(self):
        self.ledger = None


@contextmanager
def mutation_scope(broker):
    token = _active.set(broker)
    try:
        yield
    finally:
        _active.reset(token)


def current_ledger():
    broker = _active.get()
    return None if broker is None else broker.ledger


def mutation_binding():
    """Return the context lookup for reviewed barriers, without global state."""
    return _active.get


def before_attribute(value, name):
    ledger = current_ledger()
    if ledger is not None:
        ledger.attribute(value, name)


def before_contents(value):
    ledger = current_ledger()
    if ledger is not None:
        ledger.contents(value)


def before_container(value):
    """Write barrier for a reviewed plain backing container, preserving aliases."""
    ledger = current_ledger()
    if ledger is not None:
        record = ledger.record(value)
        if record is not None:
            ledger.contents(value)
        else:
            backing = ledger.backings.get(id(value))
            if backing is not None and backing[1] == 'contents':
                ledger.contents(backing[0].source)


@dataclass(eq=False, slots=True)
class MutationRecord:
    source: object
    attributes: dict | None
    storage: object = None
    storage_attribute: str | None = None
    track_attributes: bool = False
    track_contents: bool = False
    public_fields: frozenset | None = None
    attributes_saved: dict | None = None
    contents_saved: object = None
    revision: int = 0
    contents_revision: int = 0
    public_revision: int = 0
    storage_kind: str | None = None
    track_revisions: bool = True

    def current_storage(self):
        return self.source if self.storage_attribute is None else getattr(self.source, self.storage_attribute)


class MutationLedger:
    def __init__(self, counts, *, owner=None):
        self.records, self.backings = {}, {}
        self.parents, self.owner = {}, owner
        self.dirty_attributes, self.dirty_contents = [], []
        self.clock, self.counts = 0, counts
        self.player_rows = {}
        self.touched_revisions = []

    def add(self, record):
        self.records[id(record.source)] = record
        if record.attributes is not None:
            self.backings[id(record.attributes)] = record, 'attributes'
        if record.storage is not None:
            self.backings[id(record.storage)] = record, 'contents'

    def record(self, source):
        result = self.records.get(id(source))
        return result if result is not None and result.source is source else None

    def link(self, child, parent):
        a, b = self.record(child), self.record(parent)
        if a is not None and b is not None and a is not b:
            if any(not r.track_revisions and (r.attributes_saved is not None
                    or r.contents_saved is not None) for r in (a,b)):
                raise ValueError('Register revision dependencies before mutation')
            a.track_revisions = b.track_revisions = True
            self.parents.setdefault(id(child), []).append(b)

    def version(self, source, domain='value'):
        record = self.record(source)
        if record is None or not record.track_revisions:
            return None
        return (record.public_revision if domain == 'public' else
                record.contents_revision if domain == 'contents' else record.revision)

    def _propagate(self, record):
        pending = list(self.parents.get(id(record.source), ()))
        while pending:
            parent = pending.pop()
            if parent.revision == self.clock:
                continue  # Includes dependency cycles, without another visited set.
            if not parent.revision:
                self.touched_revisions.append(parent)
            parent.revision = parent.public_revision = self.clock
            self.counts['dependent_invalidations'] += 1
            pending.extend(self.parents.get(id(parent.source), ()))

    def attribute(self, source, name):
        record = self.record(source)
        if record is None:
            return
        self.attribute_record(record, name)

    def attribute_record(self, record, name):
        """Record whose ownership/identity was checked by the calling barrier."""
        self.counts['attribute_writes'] += 1
        if not record.track_revisions:
            self._save_attributes(record)
            return
        if not record.revision:
            self.touched_revisions.append(record)
        self.clock += 1
        record.revision = self.clock
        if record.public_fields is None or name in record.public_fields or name == '__dict__':
            record.public_revision = self.clock
        if name == record.storage_attribute or name == '__dict__':
            record.contents_revision = self.clock
        if record.storage is None or name == record.storage_attribute or name == '__dict__':
            self._propagate(record)
        self._save_attributes(record)

    def _save_attributes(self, record):
        if record.track_attributes and record.attributes_saved is None:
            record.attributes_saved = dict.copy(record.attributes)
            self.dirty_attributes.append(record)
            self.counts['attribute_preimages'] += 1

    def contents(self, source):
        record = self.record(source)
        if record is None:
            return
        self.contents_record(record)

    def contents_record(self, record):
        """First-write storage snapshot; revisions only for registered readers."""
        self.counts['contents_writes'] += 1
        if record.track_revisions:
            self._contents_revision(record)
        self._save_contents(record)

    def _contents_revision(self, record):
        if not record.revision:
            self.touched_revisions.append(record)
        self.clock += 1
        record.contents_revision = self.clock
        record.revision = self.clock
        self._propagate(record)

    def _save_contents(self, record):
        # A replacement backing is branch-owned: restore the attribute binding,
        # not its contents into the old root backing.
        if (record.track_contents and record.contents_saved is None
                and record.current_storage() is record.storage):
            data = record.storage
            kind = record.storage_kind
            if kind == 'list':
                saved = list(data)
            elif kind == 'deque':
                saved = tuple(data)
            elif kind == 'bytes':
                saved = bytes(data)
            elif kind == 'ordered':
                saved = OrderedDict(data.items())
            else:
                saved = set.copy(data) if type(data) is set else dict(dict.items(data))
            record.contents_saved = saved
            self.dirty_contents.append(record)
            self.counts['contents_preimages'] += 1

    def restoring(self, value):
        """Cost rollback also bypasses public mutators; invalidate its readers."""
        record = self.record(value)
        if record is not None:
            if record.attributes is not None:
                self.attribute(value, '__dict__')
            if record.storage is not None:
                self.contents(value)
        backing = self.backings.get(id(value))
        if backing is not None:
            record, kind = backing
            if kind == 'attributes':
                self.attribute(record.source, '__dict__')
            else:
                self.contents(record.source)

    def restore_attributes(self):
        for record in self.dirty_attributes:
            if object.__getattribute__(record.source, '__dict__') is not record.attributes:
                object.__setattr__(record.source, '__dict__', record.attributes)
            dict.clear(record.attributes)
            dict.update(record.attributes, record.attributes_saved)

    def restore_contents(self):
        for record in self.dirty_contents:
            if record.storage_kind == 'list':
                list.__setitem__(record.storage, slice(None), record.contents_saved)
            elif record.storage_kind == 'deque':
                deque.clear(record.storage)
                deque.extend(record.storage, record.contents_saved)
            elif record.storage_kind == 'bytes':
                bytearray.__setitem__(record.storage, slice(None), record.contents_saved)
            elif record.storage_kind == 'ordered':
                OrderedDict.clear(record.storage)
                OrderedDict.update(record.storage, record.contents_saved)
            elif type(record.storage) is set:
                set.clear(record.storage)
                set.update(record.storage, record.contents_saved)
            else:
                dict.clear(record.storage)
                dict.update(record.storage, record.contents_saved)

    def restored_root(self):
        """Call only after BOTH tracked and fallback restoration succeeded."""
        self.counts['restored_attribute_records'] += len(self.dirty_attributes)
        self.counts['restored_contents_records'] += len(self.dirty_contents)
        for record in self.dirty_attributes:
            record.attributes_saved = None
        for record in self.dirty_contents:
            record.contents_saved = None
        for record in self.touched_revisions:
            record.revision = record.contents_revision = record.public_revision = 0
        self.dirty_attributes.clear()
        self.dirty_contents.clear()
        self.touched_revisions.clear()
        self.counts['journal_rollbacks'] += 1
