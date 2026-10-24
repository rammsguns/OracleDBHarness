"""Bounded, process-local console schema results; never used by execution callers."""

from collections import OrderedDict
from threading import Lock
from time import monotonic

from harness_api.schemas import PanelResult


class SchemaPanelCache:
    def __init__(self, max_entries: int = 256) -> None:
        self._lock = Lock()
        self._entries: OrderedDict[tuple, tuple[float, PanelResult, str]] = OrderedDict()
        self._generation = 0
        self._max_entries = max_entries

    def get(self, key: tuple) -> tuple[tuple[PanelResult, str] | None, int]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                expires, panel, execution_id = entry
                if monotonic() < expires:
                    self._entries.move_to_end(key)
                    return (panel.model_copy(deep=True), execution_id), self._generation
                del self._entries[key]
            return None, self._generation

    def put(self, key: tuple, panel: PanelResult, execution_id: str, ttl: float, generation: int):
        with self._lock:
            # A concurrent admin change must not be undone by an in-flight collection.
            if generation != self._generation or ttl <= 0:
                return
            self._entries[key] = (monotonic() + ttl, panel.model_copy(deep=True), execution_id)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, profile_id: str) -> None:
        with self._lock:
            self._generation += 1
            for key in list(self._entries):
                if key[0] == profile_id:
                    del self._entries[key]
