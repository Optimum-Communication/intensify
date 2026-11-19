"""Where the CLI keeps credentials and how a repository is linked to a cloud project.

* Credentials live in ``$XDG_CONFIG_HOME/intensify/credentials.json`` (``~/.config/...``), one per
  API URL, readable only by the user. ``INTENSIFY_TOKEN`` overrides them (for CI).
* The link lives in ``.intensify/cloud.json`` next to ``intensify.yaml`` and is meant to be
  committed: it holds the API URL, organization and project, and no secrets.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LINK_FILE = Path(".intensify") / "cloud.json"
BASE_FILE = Path(".intensify") / "cloud-base.json"


class CloudConfigError(Exception):
    """Missing or inconsistent configuration; the message says how to fix it."""


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "intensify"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def normalize_url(url: str) -> str:
    return url.rstrip("/")


def _read_credentials() -> dict[str, Any]:
    path = credentials_path()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_credentials(data: dict[str, Any]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 so the token is never readable by others, even briefly.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    path.chmod(0o600)


def save_token(api_url: str, token: str) -> None:
    data = _read_credentials()
    data[normalize_url(api_url)] = {"token": token}
    _write_credentials(data)


def forget_token(api_url: str) -> None:
    data = _read_credentials()
    if data.pop(normalize_url(api_url), None) is not None:
        _write_credentials(data)


def stored_urls() -> list[str]:
    return sorted(_read_credentials())


def token_for(api_url: str) -> str | None:
    if env := os.environ.get("INTENSIFY_TOKEN"):
        return env
    entry = _read_credentials().get(normalize_url(api_url))
    return str(entry["token"]) if isinstance(entry, dict) and "token" in entry else None


@dataclass(frozen=True)
class Link:
    api_url: str
    org: str
    project: str


def read_link(spec_dir: Path) -> Link | None:
    path = spec_dir / LINK_FILE
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Link(normalize_url(data["api_url"]), data["org"], data["project"])


def write_link(spec_dir: Path, link: Link) -> Path:
    path = spec_dir / LINK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(link), indent=2) + "\n", encoding="utf-8")
    return path


def resolve_api_url(explicit: str | None, spec_dir: Path | None) -> str:
    """--api-url, then INTENSIFY_API_URL, then the repository link, then a single stored login."""
    if explicit:
        return normalize_url(explicit)
    if env := os.environ.get("INTENSIFY_API_URL"):
        return normalize_url(env)
    if spec_dir is not None and (link := read_link(spec_dir)) is not None:
        return link.api_url
    urls = stored_urls()
    if len(urls) == 1:
        return urls[0]
    raise CloudConfigError(
        "which Intensify Cloud? Pass --api-url or set INTENSIFY_API_URL"
        + (f" (logged in to: {', '.join(urls)})" if urls else "")
    )
