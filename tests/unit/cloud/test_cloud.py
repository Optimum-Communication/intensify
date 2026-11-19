from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from intensify.cli import app
from intensify.cloud import config
from intensify.cloud.client import Client, CloudError
from intensify.cloud.login import DeviceLogin, wait_for_token
from intensify.cloud.sync import Base, build_payload, pull
from intensify.spec import load_and_validate
from intensify.spec.edit import EditError
from tests.conftest import WriteSpec

ROOT = """\
intensify: "1.0"
project: {id: web}
includes: [".intensify/parts/*.yaml"]
nodes:
  - {id: web, kind: repo, lifecycle_state: active, intent: {summary: Web.}}
  - {id: web.api, kind: module, parent_id: web, intent: {summary: API.}}
"""


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("INTENSIFY_TOKEN", raising=False)
    monkeypatch.delenv("INTENSIFY_API_URL", raising=False)


@pytest.fixture
def spec(write_spec: WriteSpec) -> Path:
    path = write_spec(ROOT)
    write_spec(
        'intensify: "1.0"\nnodes:\n'
        "  - {id: web.ui, kind: module, parent_id: web, intent: {summary: UI.}}\n"
        "edges:\n  - {from: web.ui, to: web.api, type: CALLS}\n",
        ".intensify/parts/ui.yaml",
    )
    return path


# -- config ------------------------------------------------------------------------------


def test_credentials_are_private_and_per_url(tmp_path: Path) -> None:
    config.save_token("http://a.test/", "t1")
    config.save_token("http://b.test", "t2")
    path = config.credentials_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert config.token_for("http://a.test") == "t1"
    assert config.stored_urls() == ["http://a.test", "http://b.test"]
    config.forget_token("http://a.test")
    assert config.token_for("http://a.test") is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    config.save_token("http://a.test", "stored")
    monkeypatch.setenv("INTENSIFY_TOKEN", "from-ci")
    assert config.token_for("http://a.test") == "from-ci"


def test_api_url_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(config.CloudConfigError, match="Pass --api-url"):
        config.resolve_api_url(None, tmp_path)
    config.write_link(tmp_path, config.Link("http://linked.test", "org", "proj"))
    assert config.resolve_api_url(None, tmp_path) == "http://linked.test"
    monkeypatch.setenv("INTENSIFY_API_URL", "http://env.test/")
    assert config.resolve_api_url(None, tmp_path) == "http://env.test"
    assert config.resolve_api_url("http://flag.test", tmp_path) == "http://flag.test"


# -- device login ------------------------------------------------------------------------


class FakePoller:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses

    def poll_device_token(self, _code: str) -> dict[str, Any]:
        return self.responses.pop(0)


def device_login(expires_in: int = 600) -> DeviceLogin:
    return DeviceLogin("ABCD-EFGH", "u", "u?c", "dc", interval=5, expires_in=expires_in)


def test_polling_honors_slow_down() -> None:
    sleeps: list[float] = []
    poller = FakePoller(
        [{"error": "authorization_pending"}, {"error": "slow_down"}, {"access_token": "tok"}]
    )
    token = wait_for_token(poller, device_login(), sleep=sleeps.append, clock=lambda: 0.0)  # type: ignore[arg-type]
    assert token == "tok"
    assert sleeps == [5, 5, 10]


@pytest.mark.parametrize(
    ("responses", "code"),
    [
        ([{"error": "access_denied"}], "access_denied"),
        ([{"error": "expired_token"}], "expired_token"),
    ],
)
def test_polling_failures(responses: list[dict[str, Any]], code: str) -> None:
    with pytest.raises(CloudError) as exc:
        wait_for_token(FakePoller(responses), device_login(), sleep=lambda _: None)  # type: ignore[arg-type]
    assert exc.value.code == code


def test_polling_stops_at_the_deadline() -> None:
    ticks = iter([0.0, 700.0])
    with pytest.raises(CloudError, match="expired"):
        wait_for_token(
            FakePoller([{"error": "authorization_pending"}]),  # type: ignore[arg-type]
            device_login(expires_in=600),
            sleep=lambda _: None,
            clock=lambda: next(ticks),
        )


# -- push ----------------------------------------------------------------------------------


def test_payload_merges_fragments_and_drops_includes(spec: Path) -> None:
    payload = build_payload(load_and_validate(spec))
    document = payload["document"]
    assert "includes" not in document
    assert [n["id"] for n in document["nodes"]] == ["web", "web.api", "web.ui"]
    assert document["edges"] == [{"from": "web.ui", "to": "web.api", "type": "CALLS"}]
    assert sorted(f["path"] for f in payload["files"]) == [
        ".intensify/parts/ui.yaml",
        "intensify.yaml",
    ]
    assert payload["commit"] is None  # not a git repository


# -- pull ----------------------------------------------------------------------------------

PLANNED = {
    "id": "web.checkout",
    "kind": "module",
    "parent_id": "web",
    "lifecycle_state": "planned",
    "provenance": "cloud",
    "intent": {"summary": "Checkout."},
}
CHILD = {
    **PLANNED,
    "id": "web.checkout.pay",
    "parent_id": "web.checkout",
    "intent": {"summary": "Pay."},
}
EDGE = {"type": "CALLS", "to": "web.checkout", "from": "web.api", "provenance": "cloud"}


def remote(
    revision: int, nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"revision": revision, "nodes": nodes, "edges": edges or []}


def node(spec: Path, node_id: str) -> Any:
    return next((e.node for e in load_and_validate(spec).nodes if e.node.id == node_id), None)


def test_pull_adds_parents_before_children_and_edges(spec: Path) -> None:
    report = pull(load_and_validate(spec), remote(1, [CHILD, PLANNED], [EDGE]))
    assert report.added == ["web.checkout", "web.checkout.pay"]
    assert report.edges_added == 1
    text = spec.read_text()
    assert "  - {from: web.api, to: web.checkout, type: CALLS, provenance: cloud}\n" in text
    assert "    intent:\n      summary: Checkout.\n" in text
    assert Base.read(spec.parent).revision == 1
    assert not pull(load_and_validate(spec), remote(1, [CHILD, PLANNED], [EDGE])).changed


def test_pull_updates_unchanged_nodes_and_flags_conflicts(spec: Path) -> None:
    pull(load_and_validate(spec), remote(1, [PLANNED]))
    edited = {**PLANNED, "intent": {"summary": "Checkout v2."}}
    assert pull(load_and_validate(spec), remote(2, [edited])).updated == ["web.checkout"]
    assert node(spec, "web.checkout").intent.summary == "Checkout v2."

    spec.write_text(spec.read_text().replace("Checkout v2.", "Local checkout."))
    third = {**PLANNED, "intent": {"summary": "Checkout v3."}}
    report = pull(load_and_validate(spec), remote(3, [third]))
    assert report.conflicts == [
        "web.checkout: changed both locally and in the cloud; kept the local version"
    ]
    assert node(spec, "web.checkout").intent.summary == "Local checkout."
    # The conflict stays until resolved...
    assert pull(load_and_validate(spec), remote(3, [third])).conflicts
    # ...here in the cloud's favor.
    assert pull(load_and_validate(spec), remote(3, [third]), theirs=True).updated == [
        "web.checkout"
    ]
    assert node(spec, "web.checkout").intent.summary == "Checkout v3."


def test_pull_removes_withdrawn_nodes_unless_changed_locally(spec: Path) -> None:
    pull(load_and_validate(spec), remote(1, [PLANNED], [EDGE]))
    report = pull(load_and_validate(spec), remote(2, []))
    assert (report.removed, report.edges_removed) == (["web.checkout"], 1)
    assert node(spec, "web.checkout") is None

    pull(load_and_validate(spec), remote(3, [PLANNED]))
    spec.write_text(spec.read_text().replace("summary: Checkout.", "summary: Mine now."))
    report = pull(load_and_validate(spec), remote(4, []))
    assert report.conflicts == ["web.checkout: withdrawn in the cloud but changed locally; kept it"]
    assert node(spec, "web.checkout") is not None


def test_pull_respects_local_deletion_and_existing_ids(spec: Path) -> None:
    clash = {**PLANNED, "id": "web.api"}
    report = pull(load_and_validate(spec), remote(1, [clash]))
    assert report.conflicts == [
        "web.api: planned in the cloud, but a different node with this ID exists locally"
    ]
    assert node(spec, "web.api").provenance == "manual"


def test_invalid_pull_writes_nothing(spec: Path) -> None:
    before = spec.read_text()
    orphan = {**PLANNED, "parent_id": "nope"}
    with pytest.raises(EditError):
        pull(load_and_validate(spec), remote(1, [orphan]))
    assert spec.read_text() == before
    assert Base.read(spec.parent).revision is None


def test_dry_run(spec: Path) -> None:
    before = spec.read_text()
    assert pull(load_and_validate(spec), remote(1, [PLANNED]), dry_run=True).added == [
        "web.checkout"
    ]
    assert spec.read_text() == before


# -- CLI against a fake API --------------------------------------------------------------


class FakeCloud:
    """Just enough of the sync API for the CLI commands."""

    def __init__(self) -> None:
        self.pushed: list[dict[str, Any]] = []
        self.cloud: dict[str, Any] = remote(0, [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer tok":
            return httpx.Response(
                401, json={"error": "unauthorized", "message": "missing, expired or revoked token"}
            )
        path, method = request.url.path, request.method
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        routes: dict[tuple[str, str], Any] = {
            ("GET", "/v1/me"): {
                "kind": "user",
                "user": {"id": "1", "email": "a@b.c", "name": None},
                "orgs": [{"slug": "ana", "name": "Ana", "role": "owner"}],
            },
            ("POST", "/v1/orgs/ana/projects"): {
                "org": "ana",
                "slug": "shop",
                "name": "shop",
                "repos": [],
            },
            ("PUT", "/v1/orgs/ana/projects/shop/repos/web"): {
                "slug": "web",
                "name": "web",
                "latest_version": None,
            },
            ("GET", "/v1/orgs/ana/projects/shop"): {
                "org": "ana",
                "slug": "shop",
                "name": "shop",
                "repos": [
                    {"slug": "web", "name": "web", "latest_version": len(self.pushed) or None}
                ],
            },
            ("GET", "/v1/orgs/ana/projects/shop/repos/web/cloud-nodes"): self.cloud,
        }
        if (method, path) == ("POST", "/v1/orgs/ana/projects/shop/repos/web/snapshots"):
            self.pushed.append(body)
            return httpx.Response(
                201,
                json={
                    "version": len(self.pushed),
                    "unchanged": False,
                    "node_count": len(body["document"]["nodes"]),
                    "edge_count": 1,
                    "unresolved_refs": ["auth:x"],
                    "created_at": "",
                    "sha256": "",
                    "commit": None,
                },
            )
        if (method, path) in routes:
            return httpx.Response(200, json=routes[(method, path)])
        return httpx.Response(404, json={"error": "not_found", "message": f"{method} {path}"})


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    original = Client.__init__

    def init(self: Client, api_url: str, token: str | None = None, **_: Any) -> None:
        original(self, api_url, token, transport=httpx.MockTransport(fake))

    monkeypatch.setattr(Client, "__init__", init)
    config.save_token("http://cloud.test", "tok")
    return fake


runner = CliRunner()


def test_project_create_push_status_pull(spec: Path, cloud: FakeCloud) -> None:
    root = str(spec.parent)
    result = runner.invoke(
        app, ["project", "create", "shop", "--api-url", "http://cloud.test", "--spec", root]
    )
    assert result.exit_code == 0, result.output
    assert json.loads((spec.parent / ".intensify/cloud.json").read_text()) == {
        "api_url": "http://cloud.test",
        "org": "ana",
        "project": "shop",
    }

    result = runner.invoke(app, ["sync", "push", "--spec", root])
    assert result.exit_code == 0, result.output
    assert "Pushed web v1: 3 nodes, 1 edges" in result.output
    assert "unresolved cross-repository reference: auth:x" in result.output
    assert cloud.pushed[0]["document"]["project"] == {"id": "web"}

    cloud.cloud = remote(1, [PLANNED])
    result = runner.invoke(app, ["project", "status", "--spec", root])
    assert "Cloud-planned changes are waiting" in result.output
    result = runner.invoke(app, ["sync", "pull", "--spec", root])
    assert result.exit_code == 0, result.output
    assert "added web.checkout" in result.output
    assert "Up to date" in runner.invoke(app, ["project", "status", "--spec", root]).output


def test_commands_explain_missing_setup(spec: Path, cloud: FakeCloud) -> None:
    root = str(spec.parent)
    result = runner.invoke(app, ["sync", "push", "--spec", root])
    assert result.exit_code == 1
    assert "isn't linked to a cloud project" in result.output
    config.forget_token("http://cloud.test")
    result = runner.invoke(app, ["whoami", "--api-url", "http://cloud.test", "--spec", root])
    assert "not logged in" in result.output


def test_bad_token_says_how_to_fix(
    spec: Path, cloud: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTENSIFY_TOKEN", "wrong")
    result = runner.invoke(
        app, ["whoami", "--api-url", "http://cloud.test", "--spec", str(spec.parent)]
    )
    assert result.exit_code == 1
    assert "revoked token; run `intensify login`" in result.output
