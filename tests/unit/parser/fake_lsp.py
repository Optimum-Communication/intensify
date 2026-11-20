"""A fake language server for tests: answers ``textDocument/definition`` from a JSON table.

The table (``FAKE_LSP_TABLE``, a file path) maps ``"<relative path>:<line>:<character>"`` (0-based,
UTF-16) to the Location (or list of Locations) to answer. Unknown positions get ``null``.
``FAKE_LSP_DELAY`` delays every definition answer by that many seconds. ``FAKE_LSP_LOG``, if set,
gets one line per definition request, as its table key.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse


def read(stream: BinaryIO) -> dict[str, Any] | None:
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        if not line.strip():
            break
        name, _, value = line.decode().partition(":")
        if name.lower() == "content-length":
            length = int(value)
    message: dict[str, Any] = json.loads(stream.read(length))
    return message


def send(stream: BinaryIO, message: dict[str, Any]) -> None:
    body = json.dumps(message).encode()
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    stream.flush()


def main() -> None:
    table = json.loads(Path(os.environ["FAKE_LSP_TABLE"]).read_text())
    delay = float(os.environ.get("FAKE_LSP_DELAY", "0"))
    log = os.environ.get("FAKE_LSP_LOG")
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    root = Path(".")
    while (message := read(stdin)) is not None:
        method = message.get("method")
        if method == "initialize":
            root = Path(unquote(urlparse(message["params"]["rootUri"]).path)).resolve()
            send(stdout, {"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
            # A request and a notification from the server, as real servers send.
            send(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": 9000,
                    "method": "workspace/configuration",
                    "params": {"items": [{"section": "python"}]},
                },
            )
            send(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "method": "window/logMessage",
                    "params": {"type": 3, "message": "ready"},
                },
            )
        elif method == "textDocument/definition":
            params = message["params"]
            path = Path(unquote(urlparse(params["textDocument"]["uri"]).path)).resolve()
            key = f"{path.relative_to(root).as_posix()}:{params['position']['line']}:" + str(
                params["position"]["character"]
            )
            if log:
                with open(log, "a") as out:
                    out.write(key + "\n")
            time.sleep(delay)
            send(stdout, {"jsonrpc": "2.0", "id": message["id"], "result": table.get(key)})
        elif method == "shutdown":
            send(stdout, {"jsonrpc": "2.0", "id": message["id"], "result": None})
        elif method == "exit":
            return


if __name__ == "__main__":
    main()
