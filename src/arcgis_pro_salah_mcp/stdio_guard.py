"""Keep stray print() output off the MCP stdio channel.

The MCP stdio transport writes JSON-RPC to stdout. Any library that prints (arcgis progress messages,
warnings from user code in portal_python / pro_execute_code) would interleave text with the protocol.
``install()`` replaces ``sys.stdout`` with a router: writes go to a per-thread capture buffer when one is
registered (portal_python), otherwise to stderr (the client's log). ``buffer`` still points at the real
stdout, which is what FastMCP's stdio transport wraps when it starts.
"""
from __future__ import annotations

import io
import sys
import threading


class StdoutRouter(io.TextIOBase):
    def __init__(self, real) -> None:
        self._real = real
        self.buffers: dict[int, io.StringIO] = {}

    @property
    def buffer(self):  # FastMCP's stdio transport wraps this - the real stdout bytes
        return self._real.buffer

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        buf = self.buffers.get(threading.get_ident())
        if buf is not None:
            return buf.write(s)
        return sys.stderr.write(s)

    def flush(self) -> None:
        try:
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass

    def fileno(self) -> int:
        return sys.stderr.fileno()

    def isatty(self) -> bool:
        return False


def install() -> StdoutRouter:
    """Idempotent. Returns the router so callers can register capture buffers."""
    if not isinstance(sys.stdout, StdoutRouter):
        sys.stdout = StdoutRouter(sys.stdout)
    return sys.stdout
