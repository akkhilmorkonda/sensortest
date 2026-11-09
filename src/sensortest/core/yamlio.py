"""Strict YAML reading for every input file (D-045).

yaml.safe_load only (no object construction), then small typed accessors that raise TestError
with the file and field named, so a bad value never slips through YAML's implicit typing.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from sensortest.core.errors import TestError

_MISSING: Any = object()


def load_yaml(path: Path, code: str = "E301") -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TestError(code, f"file not found: {path}") from None
    except yaml.YAMLError as e:
        raise TestError(code, f"{path.name}: not valid YAML ({e})") from None
    if not isinstance(data, dict):
        raise TestError(code, f"{path.name}: top level must be a mapping")
    return data


class Fields:
    """Typed access to one YAML mapping; every error names the file and field."""

    def __init__(self, data: Any, where: str, code: str = "E301") -> None:
        if not isinstance(data, Mapping):
            raise TestError(code, f"{where}: expected a mapping, got {data!r}")
        self._d: Mapping[str, Any] = data
        self.where = where
        self.code = code

    def error(self, key: str, msg: str) -> TestError:
        return TestError(self.code, f"{self.where}.{key}: {msg}")

    def raw(self, key: str, default: Any = _MISSING) -> Any:
        if key not in self._d:
            if default is _MISSING:
                raise self.error(key, "missing")
            return default
        return self._d[key]

    def text(self, key: str, default: Any = _MISSING) -> str:
        v = self.raw(key, default)
        if not isinstance(v, str):
            raise self.error(key, f"expected text, got {v!r} (quote it in YAML)")
        return v

    def integer(self, key: str, default: Any = _MISSING) -> int:
        v = self.raw(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise self.error(key, f"expected a whole number, got {v!r}")
        return v

    def number(self, key: str, default: Any = _MISSING) -> float:
        v = self.raw(key, default)
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise self.error(key, f"expected a number, got {v!r}")
        return float(v)

    def flag(self, key: str, default: Any = _MISSING) -> bool:
        v = self.raw(key, default)
        if not isinstance(v, bool):
            raise self.error(key, f"expected true/false, got {v!r}")
        return v

    def items(self, key: str, default: Any = _MISSING) -> list[Any]:
        v = self.raw(key, default)
        if not isinstance(v, list):
            raise self.error(key, f"expected a list, got {v!r}")
        return v

    def section(self, key: str, default: Any = _MISSING) -> "Fields":
        return Fields(self.raw(key, default), f"{self.where}.{key}", self.code)

    def __iter__(self):
        return iter([str(k) for k in self._d])

    def as_dict(self) -> dict[str, Any]:
        return dict(self._d)
