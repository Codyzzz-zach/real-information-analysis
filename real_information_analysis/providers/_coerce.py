from __future__ import annotations

import math


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(value, str):
        try:
            f = float(value)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except ValueError:
            return None
    return None


def _coerce_int(value: object) -> int | None:
    """Coerce to int.

    Floats are truncated (3.9 -> 3) - timestamps and volume fields arrive
    as conceptually integral values, so this keeps the documented
    behaviour.  Strings must be integer-formatted *or* a float whose value
    is integral ("3.0" -> 3); "3.14" stays None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        try:
            return int(value)
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
        try:
            as_float = float(value)
        except ValueError:
            return None
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        if as_float.is_integer():
            return int(as_float)
        return None
    return None
