"""
Unit converter plugin.

Converts between common units without any external dependencies.

Examples the LLM would route here:
  "Convert 100 miles to kilometers"
  "How many ounces in 2.5 pounds?"
  "72°F in Celsius"
  "Convert 500MB to GB"

Enable in config.yaml:
    tools:
      plugins:
        enabled: true
        units:
          enabled: true
"""

import re

PLUGIN_NAME = "units"

# (unit_aliases, base_unit, multiplier_to_base)
_LENGTH = [
    (["mm", "millimeter", "millimeters", "millimetre", "millimetres"], "mm", 1),
    (["cm", "centimeter", "centimeters", "centimetre", "centimetres"], "mm", 10),
    (["m", "meter", "meters", "metre", "metres"], "mm", 1000),
    (["km", "kilometer", "kilometers", "kilometre", "kilometres"], "mm", 1_000_000),
    (["in", "inch", "inches"], "mm", 25.4),
    (["ft", "foot", "feet"], "mm", 304.8),
    (["yd", "yard", "yards"], "mm", 914.4),
    (["mi", "mile", "miles"], "mm", 1_609_344),
]
_WEIGHT = [
    (["mg", "milligram", "milligrams"], "mg", 1),
    (["g", "gram", "grams"], "mg", 1000),
    (["kg", "kilogram", "kilograms"], "mg", 1_000_000),
    (["oz", "ounce", "ounces"], "mg", 28_349.5),
    (["lb", "lbs", "pound", "pounds"], "mg", 453_592),
    (["t", "tonne", "tonnes", "metric ton"], "mg", 1e9),
]
_DATA = [
    (["b", "byte", "bytes"], "b", 1),
    (["kb", "kilobyte", "kilobytes"], "b", 1024),
    (["mb", "megabyte", "megabytes"], "b", 1024**2),
    (["gb", "gigabyte", "gigabytes"], "b", 1024**3),
    (["tb", "terabyte", "terabytes"], "b", 1024**4),
]

_ALL_TABLES = _LENGTH + _WEIGHT + _DATA


def _find_unit(token: str):
    """Return (base_unit, multiplier) for a token, or None."""
    t = token.lower().rstrip("s") if token.lower().endswith("s") else token.lower()
    for aliases, base, mult in _ALL_TABLES:
        if token.lower() in aliases or t in aliases:
            return base, mult
    return None


def _convert(value: float, from_tok: str, to_tok: str) -> str:
    from_info = _find_unit(from_tok)
    to_info   = _find_unit(to_tok)
    if from_info is None:
        return f"Unknown unit: '{from_tok}'"
    if to_info is None:
        return f"Unknown unit: '{to_tok}'"
    fb, fm = from_info
    tb, tm = to_info
    if fb != tb:
        return f"Can't convert between incompatible units: '{from_tok}' and '{to_tok}'"
    result = value * fm / tm
    # Format: drop trailing zeros for clean output
    if result == int(result):
        formatted = str(int(result))
    else:
        formatted = f"{result:.6g}"
    return f"{value:g} {from_tok} = **{formatted} {to_tok}**"


def _convert_temp(value: float, from_unit: str, to_unit: str) -> str:
    f = from_unit.lower()
    t = to_unit.lower()

    def to_c(v, u):
        if u in ("c", "celsius"):   return v
        if u in ("f", "fahrenheit"): return (v - 32) * 5 / 9
        if u in ("k", "kelvin"):     return v - 273.15
        return None

    def from_c(v, u):
        if u in ("c", "celsius"):   return v
        if u in ("f", "fahrenheit"): return v * 9 / 5 + 32
        if u in ("k", "kelvin"):     return v + 273.15
        return None

    celsius = to_c(value, f)
    if celsius is None:
        return f"Unknown temperature unit: '{from_unit}'"
    result = from_c(celsius, t)
    if result is None:
        return f"Unknown temperature unit: '{to_unit}'"
    return f"{value:g}°{from_unit.upper()} = **{result:.2f}°{to_unit.upper()}**"


class UnitsTool:
    """Unit converter — length, weight, data size, temperature."""

    def run(self, query: str) -> str:
        q = query.strip()

        # Temperature: "72f to c", "100 celsius to fahrenheit", "72°F in Celsius"
        temp_match = re.search(
            r"([-\d.]+)\s*°?\s*(celsius|fahrenheit|kelvin|[cfk])\b.*?\b(celsius|fahrenheit|kelvin|[cfk])\b",
            q, re.IGNORECASE,
        )
        if temp_match:
            return _convert_temp(
                float(temp_match.group(1)),
                temp_match.group(2),
                temp_match.group(3),
            )

        # General: "100 miles to km", "2.5 lb in oz"
        gen_match = re.search(
            r"([-\d.]+)\s+(\w+)\s+(?:to|in|into|as)\s+(\w+)",
            q, re.IGNORECASE,
        )
        if gen_match:
            return _convert(
                float(gen_match.group(1)),
                gen_match.group(2),
                gen_match.group(3),
            )

        return (
            "Usage: 'convert 100 miles to km', '72°F in Celsius', '500 MB to GB'.\n"
            "Supports: length, weight, data size, temperature."
        )
