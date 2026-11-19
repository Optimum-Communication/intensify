"""Push the spec to Intensify Cloud, and pull nodes planned there.

Pull is a node-level three-way merge between the last pulled cloud state (the *base*, kept in
``.intensify/cloud-base.json``), the local spec and the cloud. Only nodes and edges planned in the
cloud take part. Local edits always win a conflict, and conflicts are reported, never silently
overwritten.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from intensify import __version__
from intensify.cloud.config import BASE_FILE
from intensify.spec import LoadedSpec, yamlio
from intensify.spec.edit import EDGE_KEY_ORDER, add_node, canonical, node_to_yaml, save, to_yaml

EdgeKey = tuple[str, str, str]


def build_payload(loaded: LoadedSpec) -> dict[str, Any]:
    """The push body: one document with fragments merged in, plus the raw files."""
    root = next(f for f in loaded.files if f.is_root)
    document = yamlio.to_plain(root.data)
    document.pop("includes", None)
    document["nodes"] = [
        node for f in loaded.files for node in yamlio.to_plain(f.data.get("nodes") or [])
    ]
    document["edges"] = [
        edge for f in loaded.files for edge in yamlio.to_plain(f.data.get("edges") or [])
    ]
    spec_dir = loaded.path.parent
    files = [
        {
            "path": f.path.resolve().relative_to(spec_dir.resolve()).as_posix(),
            "content": f.path.read_text(encoding="utf-8"),
        }
        for f in loaded.files
    ]
    return {
        "document": document,
        "files": files,
        "commit": git_head(spec_dir),
        "client_version": __version__,
    }


def git_head(directory: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=directory, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip() or None


_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)$")


def web_url(remote: str) -> str | None:
    """A browsable https URL for a git remote (``git@github.com:a/b.git`` -> ``https://github.com/a/b``).

    Credentials in the remote are dropped, so they are never sent anywhere.
    """
    remote = remote.strip()
    if match := _SCP_LIKE.match(remote):
        host, path = match["host"], match["path"]
    else:
        parsed = urlsplit(remote)
        if parsed.scheme not in {"https", "http", "ssh", "git"} or not parsed.hostname:
            return None
        host, path = parsed.hostname, parsed.path.lstrip("/")
    path = path.removesuffix("/").removesuffix(".git")
    return f"https://{host}/{path}" if path else None


def git_source_url(directory: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return web_url(proc.stdout)


def git_dirty(directory: Path, paths: list[Path]) -> bool:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", *map(str, paths)],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(proc.stdout.strip())


# -- pull --------------------------------------------------------------------------------


@dataclass
class Base:
    revision: int | None = None
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[EdgeKey, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def read(cls, spec_dir: Path) -> Base:
        path = spec_dir / BASE_FILE
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            revision=data.get("revision"),
            nodes={n["id"]: n for n in data.get("nodes", [])},
            edges={_edge_key(e): e for e in data.get("edges", [])},
        )

    def write(self, spec_dir: Path) -> None:
        path = spec_dir / BASE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "revision": self.revision,
            "nodes": [self.nodes[k] for k in sorted(self.nodes)],
            "edges": [self.edges[k] for k in sorted(self.edges)],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@dataclass
class PullReport:
    revision: int
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.added or self.updated or self.removed or self.edges_added or self.edges_removed
        )


def _edge_key(edge: dict[str, Any]) -> EdgeKey:
    return (str(edge["from"]), str(edge["to"]), str(edge["type"]))


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def pull(
    loaded: LoadedSpec, remote: dict[str, Any], *, theirs: bool = False, dry_run: bool = False
) -> PullReport:
    """Merge cloud-planned nodes into the spec.

    ``theirs`` resolves conflicts in the cloud's favor. Raises ``EditError`` (and writes nothing)
    if the merged spec would be invalid.
    """
    return _Pull(loaded, remote, theirs=theirs).run(dry_run=dry_run)


class _Pull:
    def __init__(self, loaded: LoadedSpec, remote: dict[str, Any], *, theirs: bool) -> None:
        self.loaded = loaded
        self.theirs = theirs
        self.spec_dir = loaded.path.parent
        self.base = Base.read(self.spec_dir)
        self.remote_nodes = {str(n["id"]): n for n in remote.get("nodes", [])}
        self.remote_edges = {_edge_key(e): e for e in remote.get("edges", [])}
        self.report = PullReport(revision=int(remote.get("revision", 0)))
        self.new_base = Base(self.report.revision, dict(self.base.nodes), dict(self.base.edges))
        self.locations = {e.node.id: (e.file, e.index) for e in loaded.nodes}
        self.to_add: list[dict[str, Any]] = []
        self.to_replace: dict[str, dict[str, Any]] = {}
        self.to_remove: set[str] = set()

    def run(self, *, dry_run: bool) -> PullReport:
        for node_id in sorted(set(self.base.nodes) | set(self.remote_nodes)):
            self._plan(node_id)
        self._apply_nodes()
        self._merge_edges()
        if not dry_run:
            if self.report.changed:
                save(self.loaded)
            self.new_base.write(self.spec_dir)
        return self.report

    def _local(self, node_id: str) -> dict[str, Any] | None:
        where = self.locations.get(node_id)
        if where is None:
            return None
        plain: dict[str, Any] = yamlio.to_plain(where[0].data["nodes"][where[1]])
        return plain

    def _conflict(self, node_id: str, message: str) -> None:
        self.report.conflicts.append(f"{node_id}: {message}")

    def _plan(self, node_id: str) -> None:
        base, remote, mine = (
            self.base.nodes.get(node_id),
            self.remote_nodes.get(node_id),
            self._local(node_id),
        )
        if remote is not None and base is None:  # newly planned in the cloud
            if mine is None:
                self.to_add.append(remote)
            elif not _same(mine, remote):
                if not self.theirs:
                    self._conflict(
                        node_id,
                        "planned in the cloud, but a different node with this ID exists locally",
                    )
                    return
                self.to_replace[node_id] = remote
            self.new_base.nodes[node_id] = remote
        elif remote is not None and base is not None:  # known: maybe changed on either side
            unchanged = mine is None or _same(remote, base) or _same(mine, remote)
            if not unchanged:
                if not (_same(mine, base) or self.theirs):
                    self._conflict(
                        node_id, "changed both locally and in the cloud; kept the local version"
                    )
                    return
                self.to_replace[node_id] = remote
            self.new_base.nodes[node_id] = remote
        elif base is not None:  # withdrawn in the cloud
            if mine is not None:
                if not (_same(mine, base) or self.theirs):
                    self._conflict(node_id, "withdrawn in the cloud but changed locally; kept it")
                    return
                self.to_remove.add(node_id)
            self.new_base.nodes.pop(node_id, None)

    def _apply_nodes(self) -> None:
        # Parents first, so a node planned under another new node finds its parent.
        pending = {str(n["id"]): n for n in self.to_add}
        while pending:
            ready = [n for n in pending.values() if n.get("parent_id") not in pending]
            for node in sorted(ready or list(pending.values()), key=lambda n: str(n["id"])):
                add_node(self.loaded, node)
                self.report.added.append(str(node["id"]))
                pending.pop(str(node["id"]))
        for node_id, node in self.to_replace.items():
            file, index = self.locations[node_id]
            file.data["nodes"][index] = node_to_yaml(node)
            self.report.updated.append(node_id)
        for file in self.loaded.files:
            seq = file.data.get("nodes") or []
            for index in reversed(range(len(seq))):
                if str(seq[index].get("id")) in self.to_remove:
                    self.report.removed.append(str(seq[index].get("id")))
                    del seq[index]

    def _merge_edges(self) -> None:
        local: dict[EdgeKey, tuple[Any, Any]] = {}
        for file in self.loaded.files:
            for item in file.data.get("edges") or []:
                local[_edge_key(yamlio.to_plain(item))] = (file, item)
        root = next(f for f in self.loaded.files if f.is_root)
        for key in sorted(set(self.remote_edges) - set(self.base.edges)):
            if key not in local:
                if root.data.get("edges") is None:
                    root.data["edges"] = to_yaml([])
                root.data["edges"].append(
                    to_yaml(canonical(self.remote_edges[key], EDGE_KEY_ORDER))
                )
                self.report.edges_added += 1
            self.new_base.edges[key] = self.remote_edges[key]
        for key in sorted(set(self.base.edges) - set(self.remote_edges)):
            if key in local:
                file, item = local[key]
                seq = file.data["edges"]
                seq.pop(next(i for i, existing in enumerate(seq) if existing is item))
                self.report.edges_removed += 1
            self.new_base.edges.pop(key, None)
