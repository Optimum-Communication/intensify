"""A small client for the sync API (spec/sync-api/v1/openapi.yaml)."""

from __future__ import annotations

from typing import Any

import httpx

from intensify import __version__


class CloudError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or []

    def __str__(self) -> str:
        text = super().__str__()
        if self.status == 401:  # noqa: PLR2004
            text += "; run `intensify login`"
        return text + "".join(f"\n  {d}" for d in self.details)


class Client:
    def __init__(
        self,
        api_url: str,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"user-agent": f"intensify-cli/{__version__}"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=api_url, headers=headers, timeout=60.0, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(self, method: str, path: str, json: Any = None) -> Any:
        try:
            response = self._http.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise CloudError(
                0, "network", f"could not reach {self._http.base_url}: {exc}"
            ) from None
        if response.status_code == 204:  # noqa: PLR2004
            return None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error:
            raise CloudError(
                response.status_code,
                str(body.get("error", "http_error")),
                str(body.get("message", body.get("error", response.reason_phrase))),
                [str(d) for d in body.get("details", [])],
            )
        return body

    # -- auth --------------------------------------------------------------------------

    def start_device_login(self, client_name: str) -> dict[str, Any]:
        result: dict[str, Any] = self.request(
            "POST", "/v1/auth/device/code", {"client_name": client_name}
        )
        return result

    def poll_device_token(self, device_code: str) -> dict[str, Any]:
        """The token, or ``{"error": ...}`` while pending (not raised: it's part of the flow)."""
        response = self._http.post("/v1/auth/device/token", json={"device_code": device_code})
        body: dict[str, Any] = response.json()
        return body

    def me(self) -> dict[str, Any]:
        result: dict[str, Any] = self.request("GET", "/v1/me")
        return result

    def revoke_current_token(self) -> None:
        self.request("DELETE", "/v1/tokens/current")

    # -- projects ----------------------------------------------------------------------

    def create_project(self, org: str, slug: str, name: str | None) -> dict[str, Any]:
        body = {"slug": slug, **({"name": name} if name else {})}
        result: dict[str, Any] = self.request("POST", f"/v1/orgs/{org}/projects", body)
        return result

    def get_project(self, org: str, project: str) -> dict[str, Any]:
        result: dict[str, Any] = self.request("GET", f"/v1/orgs/{org}/projects/{project}")
        return result

    def link_repo(
        self, org: str, project: str, repo: str, name: str | None, source_url: str | None = None
    ) -> dict[str, Any]:
        body = {k: v for k, v in {"name": name, "source_url": source_url}.items() if v is not None}
        result: dict[str, Any] = self.request(
            "PUT", f"/v1/orgs/{org}/projects/{project}/repos/{repo}", body
        )
        return result

    def create_project_token(self, org: str, project: str, name: str) -> dict[str, Any]:
        result: dict[str, Any] = self.request(
            "POST", f"/v1/orgs/{org}/projects/{project}/tokens", {"name": name}
        )
        return result

    # -- sync --------------------------------------------------------------------------

    def push(self, org: str, project: str, repo: str, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = self.request(
            "POST", f"/v1/orgs/{org}/projects/{project}/repos/{repo}/snapshots", payload
        )
        return result

    def cloud_nodes(self, org: str, project: str, repo: str) -> dict[str, Any]:
        result: dict[str, Any] = self.request(
            "GET", f"/v1/orgs/{org}/projects/{project}/repos/{repo}/cloud-nodes"
        )
        return result

    def latest_snapshot(self, org: str, project: str, repo: str) -> dict[str, Any] | None:
        try:
            result: dict[str, Any] = self.request(
                "GET", f"/v1/orgs/{org}/projects/{project}/repos/{repo}/snapshots/latest"
            )
        except CloudError as exc:
            if exc.status == 404:  # noqa: PLR2004
                return None
            raise
        return result
