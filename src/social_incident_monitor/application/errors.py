"""Application-level errors. Adapters translate their failures into these types."""

from __future__ import annotations


class MonitorError(Exception):
    """Base class for expected application errors."""


class StorageError(MonitorError):
    """Persistent storage could not complete an operation (disk full, permission denied, ...)."""


class NotFoundError(MonitorError):
    pass


class LifecycleError(MonitorError):
    """A requested state transition is not allowed (e.g. acknowledging a resolved incident)."""


class ConfigurationError(MonitorError):
    pass
