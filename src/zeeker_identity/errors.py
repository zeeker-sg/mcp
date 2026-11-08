"""Operator-facing failures without source data or filesystem details."""


class RegistryError(Exception):
    """A rejected operation; no partial transaction should be retained."""


class Conflict(RegistryError):
    """The reviewed revision or ownership no longer matches."""
