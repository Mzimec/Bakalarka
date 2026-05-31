from typing import Protocol

def keyed_obj_list_to_dict(objs: list[Any]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for obj in objs:
        if res.get(obj.key):
            raise RuntimeWarning(f"  Trying to add object with key: '{obj.key}' that is already present in the dictionary.")
        res[obj.key] = obj
    return res