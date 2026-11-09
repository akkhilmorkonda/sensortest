"""A named registry: the single mechanism behind every plugin point (D-033).

Metrics, devices, sinks, UI actions and panels each get one Registry. Plugins add themselves
with a decorator at import time; the composition root (app/) imports the plugin packages once.
"""

from collections.abc import Callable, Iterator


class RegistryError(LookupError):
    """Raised for a duplicate registration or an unknown name. Always names the registry."""


class Registry[T]:
    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        """Decorator: ``@METRICS.register("BL_OFFSET")``. Duplicate names fail at import time."""

        def decorator(obj: T) -> T:
            if name in self._items:
                raise RegistryError(f"{self._kind} {name!r} is already registered")
            self._items[name] = obj
            return obj

        return decorator

    def get(self, name: str) -> T:
        """Look up by name. An unknown name fails fast and lists what is registered."""
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "none"
            raise RegistryError(f"unknown {self._kind} {name!r} (registered: {known})") from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._items))

    def __len__(self) -> int:
        return len(self._items)
