"""Inline Dyson cloud setup flow driven by the UC setup wizard.

Step 1 — collect email + password + region (handled by driver.json schema).
Step 2 — call login_email_otp to trigger OTP send.
Step 3 — RequestUserInput for the OTP code.
Step 4 — verify and return per-device credentials.

We never persist the account email/password — only the per-device local
credential is saved to integration config.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from libdyson.cloud import DysonAccount

_LOG = logging.getLogger(__name__)


class DysonSetupError(Exception):
    pass


class DysonCloudSetup:
    """Holds the verify callable between setup steps so we don't re-prompt for
    the password after the OTP comes in."""

    def __init__(self) -> None:
        self._verify: Callable[[str, str], dict] | None = None
        self._password: str | None = None
        # Optional static LAN IP carried across the OTP round-trip so the
        # final SetupComplete can apply it to every device fetched from the
        # cloud — same field works for users whose fan is on a VLAN where
        # mDNS doesn't reach the UCR3.
        self.static_ip: str | None = None

    def request_otp(self, email: str, password: str, region: str) -> None:
        if not email or not password:
            raise DysonSetupError("Email and password are required")
        account = DysonAccount()
        try:
            self._verify = account.login_email_otp(email, region or "GB")
        except Exception as exc:
            _LOG.error("dyson login_email_otp failed: %s", exc)
            raise DysonSetupError(f"Login failed: {exc}") from exc
        self._password = password

    def verify_otp(self, otp: str) -> list[dict[str, Any]]:
        if self._verify is None or self._password is None:
            raise DysonSetupError("OTP requested out of order — restart setup")
        try:
            auth_info = self._verify(otp.strip(), self._password)
        except Exception as exc:
            _LOG.error("dyson otp verify failed: %s", exc)
            raise DysonSetupError(f"OTP verification failed: {exc}") from exc
        # Wipe the password from memory now that it has served its purpose.
        self._password = None
        self._verify = None

        account = DysonAccount(auth_info)
        devices = account.devices()
        return [
            {
                "serial": d.serial,
                "name": d.name,
                "product_type": d.product_type,
                "credential": d.credential,
            }
            for d in devices
        ]
