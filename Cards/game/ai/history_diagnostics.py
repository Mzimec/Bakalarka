"""Describe incompatible *observer* evidence without recording card values."""


def difference_path(actual, expected, path=""):
    """!
    @brief Locate a deterministic structural mismatch for continuation diagnostics.
    """
    if type(actual) is not type(expected):
        return path or "/"
    if isinstance(actual, dict):
        for key in sorted(actual.keys() | expected.keys()):
            child = f"{path}/{key}"
            if key not in actual or key not in expected:
                return child
            if actual[key] != expected[key]:
                return difference_path(actual[key], expected[key], child)
    elif isinstance(actual, list):
        for index, (left, right) in enumerate(zip(actual, expected)):
            if left != right:
                return difference_path(left, right, f"{path}/{index}")
        if len(actual) != len(expected):
            return f"{path}/length"
    return path or "/"


def recall_mismatch(actual, expected):
    """!
    @brief Describe the first differing immutable recall entry.
    """
    for left, right in zip(actual, expected):
        if left != right:
            if left.kind != right.kind:
                return "recall/kind"
            return left.kind + difference_path(left.describe(), right.describe())
    return "recall/length"
