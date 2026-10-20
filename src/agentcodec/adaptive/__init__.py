"""Opt-in local adaptive selection; importing this package performs no work."""

__all__ = ["select_encoding"]


def __getattr__(name):
    if name == "select_encoding":
        from .selector import select_encoding
        return select_encoding
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
