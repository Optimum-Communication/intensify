"""Intensify Cloud commands: login, logout, whoami, project, sync."""

from __future__ import annotations

import os
import platform
import webbrowser
from pathlib import Path
from typing import Annotated, Any

import typer

from intensify.cli.node import load_or_exit
from intensify.cloud import login as device
from intensify.cloud.client import Client, CloudError
from intensify.cloud.config import (
    LINK_FILE,
    CloudConfigError,
    Link,
    forget_token,
    read_link,
    resolve_api_url,
    save_token,
    token_for,
    write_link,
)
from intensify.cloud.sync import Base, build_payload, git_dirty, git_source_url, pull
from intensify.spec import LoadedSpec
from intensify.spec.edit import EditError
from intensify.spec.loader import resolve_path

project_app = typer.Typer(
    help="Link this repository to an Intensify Cloud project.", no_args_is_help=True
)
sync_app = typer.Typer(
    help="Push the spec to Intensify Cloud and pull nodes planned there.", no_args_is_help=True
)

SpecOption = Annotated[
    Path, typer.Option("--spec", "-s", help="Spec file, or a directory containing intensify.yaml.")
]
ApiUrlOption = Annotated[
    str | None, typer.Option("--api-url", help="Intensify Cloud API URL (or INTENSIFY_API_URL).")
]


def _fail(message: object, code: int = 1) -> None:
    typer.secho(str(message), fg="red", err=True)
    raise typer.Exit(code)


def _spec_dir(spec: Path) -> Path:
    return resolve_path(spec).parent


def _client(api_url: str, *, require_token: bool = True) -> Client:
    token = token_for(api_url)
    if require_token and not token:
        _fail(f"not logged in to {api_url}; run `intensify login --api-url {api_url}`")
    return Client(api_url, token)


def _linked(spec: Path) -> tuple[LoadedSpec, Link, Client]:
    loaded = load_or_exit(spec)
    link = read_link(loaded.path.parent)
    if link is None:
        _fail(
            "this repository isn't linked to a cloud project; "
            "run `intensify project create` or `intensify project link`"
        )
    assert link is not None
    api_url = os.environ.get("INTENSIFY_API_URL") or link.api_url
    return loaded, link, _client(api_url)


def _repo_slug(loaded: LoadedSpec) -> str:
    assert loaded.document is not None
    return loaded.document.project.id


# -- login ---------------------------------------------------------------------------------


def login(
    api_url: ApiUrlOption = None,
    spec: SpecOption = Path("."),
    open_browser: Annotated[
        bool, typer.Option("--browser/--no-browser", help="Open the approval page.")
    ] = True,
) -> None:
    """Sign in to Intensify Cloud in your browser (OAuth device flow)."""
    try:
        url = resolve_api_url(api_url, _spec_dir(spec))
    except CloudConfigError as exc:
        _fail(exc, 2)
    with Client(url) as client:
        try:
            pending = device.start(client, f"intensify CLI on {platform.node() or 'this computer'}")
            typer.echo(f"To sign in, open {pending.verification_uri}")
            typer.echo("and confirm this code: " + typer.style(pending.user_code, bold=True))
            if open_browser:
                webbrowser.open(pending.verification_uri_complete)
            typer.echo("Waiting for approval...")
            token = device.wait_for_token(client, pending)
        except CloudError as exc:
            _fail(exc)
    save_token(url, token)
    with Client(url, token) as client:
        me = client.me()
    orgs = ", ".join(o["slug"] for o in me.get("orgs", []))
    typer.secho(
        f"Logged in to {url} as {me['user']['email']}"
        + (f" (organizations: {orgs})" if orgs else ""),
        fg="green",
    )


def logout(api_url: ApiUrlOption = None, spec: SpecOption = Path(".")) -> None:
    """Revoke this computer's token and forget it."""
    try:
        url = resolve_api_url(api_url, _spec_dir(spec))
    except CloudConfigError as exc:
        _fail(exc, 2)
    if os.environ.get("INTENSIFY_TOKEN"):
        _fail("INTENSIFY_TOKEN is set; unset it to log out a stored login", 2)
    if token_for(url):
        with Client(url, token_for(url)) as client:
            try:
                client.revoke_current_token()
            except CloudError as exc:
                if exc.status != 401:  # noqa: PLR2004 - already revoked or expired is fine
                    _fail(exc)
    forget_token(url)
    typer.secho(f"Logged out of {url}", fg="green")


def whoami(api_url: ApiUrlOption = None, spec: SpecOption = Path(".")) -> None:
    """Show who you are logged in as."""
    try:
        url = resolve_api_url(api_url, _spec_dir(spec))
    except CloudConfigError as exc:
        _fail(exc, 2)
    with _client(url) as client:
        try:
            me = client.me()
        except CloudError as exc:
            _fail(exc)
    if me["kind"] == "project":
        typer.echo(f"Project token for {me['project']['org']}/{me['project']['slug']} on {url}")
        return
    typer.echo(f"{me['user']['email']} on {url}")
    for org in me["orgs"]:
        typer.echo(f"  {org['slug']} ({org['role']})")


# -- project -------------------------------------------------------------------------------


def _default_org(client: Client) -> str:
    orgs = client.me().get("orgs", [])
    if len(orgs) != 1:
        names = ", ".join(o["slug"] for o in orgs) or "none"
        _fail(f"pass --org (your organizations: {names})", 2)
    return str(orgs[0]["slug"])


def _link_here(loaded: LoadedSpec, client: Client, url: str, org: str, project: str) -> None:
    name = loaded.document.project.name if loaded.document else None
    source = git_source_url(loaded.path.parent)
    repo = client.link_repo(org, project, _repo_slug(loaded), name, source)
    path = write_link(loaded.path.parent, Link(url, org, project))
    typer.secho(f"Linked repository {repo['slug']} to {org}/{project}", fg="green")
    typer.echo(
        f"Wrote {os.path.relpath(path)}; commit it so your team and CI use the same project."
    )


@project_app.command("create")
def project_create(
    slug: Annotated[str, typer.Argument(help="Project slug (lowercase).")],
    org: Annotated[
        str | None, typer.Option(help="Organization. Defaults to your only one.")
    ] = None,
    name: Annotated[str | None, typer.Option(help="Display name.")] = None,
    api_url: ApiUrlOption = None,
    spec: SpecOption = Path("."),
) -> None:
    """Create a cloud project and link this repository to it."""
    loaded = load_or_exit(spec)
    try:
        url = resolve_api_url(api_url, loaded.path.parent)
    except CloudConfigError as exc:
        _fail(exc, 2)
    with _client(url) as client:
        try:
            org = org or _default_org(client)
            client.create_project(org, slug, name)
            typer.secho(f"Created project {org}/{slug}", fg="green")
            _link_here(loaded, client, url, org, slug)
        except CloudError as exc:
            _fail(exc)


@project_app.command("link")
def project_link(
    target: Annotated[
        str, typer.Argument(help="ORG/PROJECT, or PROJECT with your only organization.")
    ],
    api_url: ApiUrlOption = None,
    spec: SpecOption = Path("."),
) -> None:
    """Add this repository to an existing cloud project."""
    loaded = load_or_exit(spec)
    try:
        url = resolve_api_url(api_url, loaded.path.parent)
    except CloudConfigError as exc:
        _fail(exc, 2)
    with _client(url) as client:
        try:
            org, _, project = target.rpartition("/")
            _link_here(loaded, client, url, org or _default_org(client), project)
        except CloudError as exc:
            _fail(exc)


@project_app.command("status")
def project_status(spec: SpecOption = Path(".")) -> None:
    """Show the linked project, its repositories, and what's waiting to be pulled."""
    loaded, link, client = _linked(spec)
    repo = _repo_slug(loaded)
    with client:
        try:
            project = client.get_project(link.org, link.project)
            remote = client.cloud_nodes(link.org, link.project, repo)
        except CloudError as exc:
            _fail(exc)
    typer.echo(f"{link.org}/{link.project} on {link.api_url}")
    for r in project["repos"]:
        version = f"v{r['latest_version']}" if r["latest_version"] is not None else "never pushed"
        marker = " (this repository)" if r["slug"] == repo else ""
        typer.echo(f"  {r['slug']}: {version}{marker}")
    base = Base.read(loaded.path.parent)
    if remote["revision"] != base.revision:
        typer.secho(
            "Cloud-planned changes are waiting: run `intensify sync pull` "
            f"({len(remote['nodes'])} planned nodes).",
            fg="yellow",
        )
    else:
        typer.echo("Up to date with nodes planned in the cloud.")


@project_app.command("token")
def project_token(
    name: Annotated[str, typer.Argument(help="What will use it, e.g. 'GitHub Actions'.")],
    spec: SpecOption = Path("."),
) -> None:
    """Create a project token for CI (set it as INTENSIFY_TOKEN)."""
    _, link, client = _linked(spec)
    with client:
        try:
            created = client.create_project_token(link.org, link.project, name)
        except CloudError as exc:
            _fail(exc)
    typer.echo(created["token"])
    typer.secho(
        "Store this as the INTENSIFY_TOKEN secret in CI. It won't be shown again.",
        fg="yellow",
        err=True,
    )


# -- sync ----------------------------------------------------------------------------------


@sync_app.command("push")
def sync_push(spec: SpecOption = Path(".")) -> None:
    """Upload intensify.yaml (and fragments) to the linked project. Source code is never sent."""
    loaded, link, client = _linked(spec)
    repo = _repo_slug(loaded)
    if git_dirty(loaded.path.parent, [f.path for f in loaded.files]):
        typer.secho(
            "warning: the spec has uncommitted changes; pushing them anyway", fg="yellow", err=True
        )
    with client:
        try:
            result = client.push(link.org, link.project, repo, build_payload(loaded))
        except CloudError as exc:
            _fail(exc)
    if result["unchanged"]:
        typer.echo(f"Unchanged: {repo} is already at v{result['version']}.")
    else:
        typer.secho(
            f"Pushed {repo} v{result['version']}: "
            f"{result['node_count']} nodes, {result['edge_count']} edges",
            fg="green",
        )
    for ref in result.get("unresolved_refs", []):
        typer.secho(f"  unresolved cross-repository reference: {ref}", fg="yellow")


@sync_app.command("pull")
def sync_pull(
    spec: SpecOption = Path("."),
    theirs: Annotated[
        bool, typer.Option("--theirs", help="Resolve conflicts in the cloud's favor.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show what would change.")
    ] = False,
) -> None:
    """Merge nodes and edges planned in the cloud into intensify.yaml."""
    loaded, link, client = _linked(spec)
    with client:
        try:
            remote: dict[str, Any] = client.cloud_nodes(link.org, link.project, _repo_slug(loaded))
        except CloudError as exc:
            _fail(exc)
    try:
        report = pull(loaded, remote, theirs=theirs, dry_run=dry_run)
    except EditError as exc:
        _fail(f"the cloud's changes don't fit this spec, so nothing was written: {exc}")
    verb = "Would pull" if dry_run else "Pulled"
    for label, items in (
        ("added", report.added),
        ("updated", report.updated),
        ("removed", report.removed),
    ):
        for node_id in items:
            typer.echo(f"  {label} {node_id}")
    if report.edges_added or report.edges_removed:
        typer.echo(f"  edges: +{report.edges_added} -{report.edges_removed}")
    for conflict in report.conflicts:
        typer.secho(f"  conflict: {conflict}", fg="yellow")
    if report.changed:
        typer.secho(f"{verb} cloud revision {report.revision}.", fg="green")
    else:
        typer.echo("Nothing to pull.")
    if report.conflicts:
        typer.secho(
            "Resolve conflicts by editing the nodes, or pull again with --theirs.", fg="yellow"
        )
        raise typer.Exit(1)


__all__ = ["LINK_FILE", "login", "logout", "project_app", "sync_app", "whoami"]
