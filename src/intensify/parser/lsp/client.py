"""A minimal Language Server Protocol client: JSON-RPC over a server's stdin and stdout.

Only what call resolution needs: ``initialize``, ``textDocument/didOpen``,
``textDocument/definition`` and a clean shutdown. Requests from the server (configuration,
progress, capability registration) get empty answers so servers that wait for them carry on.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, cast


class LspError(Exception):
    """The server could not be started, or stopped answering."""


_SHUTDOWN_TIMEOUT = 2.0


class Client:
    def __init__(self, command: Sequence[str], root: Path) -> None:
        try:
            self._proc = subprocess.Popen(
                list(command),
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise LspError(f"could not start {command[0]}: {exc}") from exc
        self._stdin = cast(BinaryIO, self._proc.stdin)
        self._next_id = 0
        self._results: dict[int, Any] = {}
        self._done = threading.Condition()
        self._closed = False
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- protocol --------------------------------------------------------------------

    def initialize(self, root: Path, timeout: float) -> None:
        uri = root.resolve().as_uri()
        request = self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": uri,
                "rootPath": str(root.resolve()),
                "workspaceFolders": [{"uri": uri, "name": root.name}],
                "capabilities": {
                    "textDocument": {"definition": {"linkSupport": True}},
                    "workspace": {"configuration": True, "workspaceFolders": True},
                },
            },
        )
        if self.wait(request, time.monotonic() + timeout) is _TIMEOUT:
            raise LspError("no answer to initialize")
        self.notify("initialized", {})

    def open(self, uri: str, language_id: str, source: str) -> None:
        self.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": language_id, "version": 1, "text": source}},
        )

    def definition(self, uri: str, line: int, character: int) -> int:
        """Send a definition request (0-based line, UTF-16 character); returns its ID."""
        return self.request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            request = self.request("shutdown", None)
            self.wait(request, time.monotonic() + _SHUTDOWN_TIMEOUT)
            self.notify("exit", None)
        with contextlib.suppress(Exception):
            self._stdin.close()
        try:
            self._proc.wait(timeout=_SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

    # -- JSON-RPC --------------------------------------------------------------------

    def request(self, method: str, params: Any) -> int:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        return self._next_id

    def notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def wait(self, request: int, deadline: float) -> Any:
        """The result of ``request``, or ``_TIMEOUT`` if it doesn't come by ``deadline``."""
        with self._done:
            while request not in self._results:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._reader.is_alive():
                    return _TIMEOUT
                self._done.wait(remaining)
            return self._results.pop(request)

    def _send(self, message: dict[str, Any]) -> None:
        body = json.dumps(message).encode()
        with self._write_lock:
            try:
                self._stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                self._stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise LspError("the server exited") from exc

    def _read(self) -> None:
        stream = cast(BinaryIO, self._proc.stdout)
        while True:
            message = _read_message(stream)
            if message is None:
                break
            if "method" in message and "id" in message:
                self._answer(message)
            elif "id" in message:
                with self._done:
                    self._results[message["id"]] = message.get("result")
                    self._done.notify_all()
        with self._done:
            self._done.notify_all()

    def _answer(self, message: dict[str, Any]) -> None:
        """Reply to a request from the server with the empty answer it accepts."""
        result: Any = None
        if message["method"] == "workspace/configuration":
            result = [None] * len(message.get("params", {}).get("items", []))
        elif message["method"] == "workspace/workspaceFolders":
            result = []
        with contextlib.suppress(LspError):
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})


class _Timeout:
    def __repr__(self) -> str:
        return "TIMEOUT"


_TIMEOUT: Any = _Timeout()
TIMEOUT = _TIMEOUT
"""Returned by ``Client.wait`` when no answer came in time."""


def _read_message(stream: BinaryIO) -> dict[str, Any] | None:
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        name, _, value = line.decode("ascii", errors="replace").partition(":")
        if name.lower() == "content-length":
            length = int(value.strip())
    if length is None:
        return None
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        message = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
