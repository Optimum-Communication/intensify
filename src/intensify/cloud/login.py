"""``intensify login``: the OAuth 2.0 device authorization grant (RFC 8628), client side."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from intensify.cloud.client import Client, CloudError

SLOW_DOWN_SECONDS = 5


@dataclass(frozen=True)
class DeviceLogin:
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    device_code: str
    interval: int
    expires_in: int


def start(client: Client, client_name: str) -> DeviceLogin:
    data = client.start_device_login(client_name)
    return DeviceLogin(
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        verification_uri_complete=data["verification_uri_complete"],
        device_code=data["device_code"],
        interval=int(data["interval"]),
        expires_in=int(data["expires_in"]),
    )


def wait_for_token(
    client: Client,
    login: DeviceLogin,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Poll until the user approves; raises CloudError if they deny or the code expires."""
    deadline = clock() + login.expires_in
    interval = login.interval
    while True:
        sleep(interval)
        result = client.poll_device_token(login.device_code)
        if token := result.get("access_token"):
            return str(token)
        error = result.get("error")
        if error == "slow_down":
            interval += SLOW_DOWN_SECONDS
        elif error == "access_denied":
            raise CloudError(400, "access_denied", "the login was denied in the browser")
        elif error != "authorization_pending" or clock() >= deadline:
            raise CloudError(
                400, "expired_token", "the login code expired; run `intensify login` again"
            )
