"""Capture native index groups only when a proven cost transaction writes them.

Native providers have fixed group keys. Updates mutate buckets inside a group;
they do not mutate the outer mapping. Unknown providers retain graph snapshots.
Hooks belong to one provider/transaction, never to a global or shared registry.
"""
from helper.query_system.object_register import SimpleObjectIndexProvider


def defer_index_groups(checkpoint, provider):
    if (type(provider) is not SimpleObjectIndexProvider
            or provider.__dict__.keys() != {"_data", "_ordered_keys"}
            or type(provider._data) is not dict
            or any(type(group) is not dict for group in provider._data.values())):
        return False

    update = provider._process_register_update
    clear = provider.clear

    def before_update(idx, context, operation):
        for key in context.data:
            group = provider._data.get(key)
            if group is not None and id(group) not in checkpoint._seen:
                checkpoint.watch(group, root=True)
        return update(idx, context, operation)

    def before_clear():
        for group in provider._data.values():
            if id(group) not in checkpoint._seen:
                checkpoint.watch(group, root=True)
        return clear()

    # The normal traversal already saved the provider's attributes, before
    # these hooks existed. The fixed outer mapping needs no separate snapshot.
    checkpoint._seen.add(id(provider._data))
    provider._process_register_update = before_update
    provider.clear = before_clear
    checkpoint._index_hooks.append((provider, before_update, before_clear))
    return True


def close_index_groups(checkpoint):
    for provider, update, clear in checkpoint._index_hooks:
        if provider.__dict__.get("_process_register_update") is update:
            del provider._process_register_update
        if provider.__dict__.get("clear") is clear:
            del provider.clear
    checkpoint._index_hooks.clear()
