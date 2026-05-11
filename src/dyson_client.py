"""Thin async wrapper around libdyson's sync MQTT client.

libdyson runs its own MQTT thread; we bridge state-change callbacks into
asyncio via `call_soon_threadsafe` so UC entity attribute updates always
happen on the integration's main loop.

One DysonClient per configured device. Maintains the libdyson connection,
re-resolves the host over mDNS on disconnect, and re-attempts every 10 s
on connection loss without exiting the daemon.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Awaitable, Callable

from libdyson import get_device

_LOG = logging.getLogger(__name__)

_RECONNECT_DELAY = 3.0
_ALIVE_POLL = 1.0


class DysonClient:
    def __init__(
        self,
        serial: str,
        credential: str,
        product_type: str,
        on_state_change: Callable[[], Awaitable[None] | None],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.serial = serial
        self.credential = credential
        self.product_type = product_type
        self._on_state_change = on_state_change
        self._loop = loop or asyncio.get_event_loop()
        self._device = None
        self._connected = False
        self._stopped = False
        self._task: asyncio.Task | None = None
        # Cache the IP across reconnect cycles so we skip mDNS resolution
        # on every retry — mDNS lookup is 500ms-2s and the IP rarely
        # changes mid-session.
        self._cached_ip: str | None = None

    @property
    def device(self):
        return self._device

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = self._loop.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._device is not None:
            try:
                self._device.disconnect()
            except Exception as exc:
                _LOG.debug("disconnect raised: %s", exc)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def _resolve_host(self) -> str | None:
        # Try the cached IP first — mDNS adds 500ms-2s and is unnecessary
        # if the device hasn't moved. We only re-resolve on cold start or
        # after a cached-IP connect fails.
        if self._cached_ip:
            return self._cached_ip
        # Dyson serials encode region + variant (e.g. "AAA-XX-ZZZ0000A"); mDNS hostname is lowercased.
        hostname = f"{self.serial.lower()}.local"
        try:
            ip = socket.gethostbyname(hostname)
            self._cached_ip = ip
            return ip
        except OSError as exc:
            _LOG.warning("mDNS resolve failed for %s: %s", hostname, exc)
            return None

    def _on_message(self, _msg_type) -> None:
        # libdyson invokes this on its MQTT thread.
        if self._loop.is_closed():
            return
        cb = self._on_state_change
        if asyncio.iscoroutinefunction(cb):
            self._loop.call_soon_threadsafe(
                lambda: self._loop.create_task(cb())
            )
        else:
            self._loop.call_soon_threadsafe(cb)

    async def _run(self) -> None:
        while not self._stopped:
            host = self._resolve_host()
            if not host:
                await asyncio.sleep(_RECONNECT_DELAY)
                continue
            device = get_device(self.serial, self.credential, self.product_type)
            if device is None:
                _LOG.error(
                    "libdyson does not recognise product_type=%s — aborting",
                    self.product_type,
                )
                return
            device.add_message_listener(self._on_message)
            try:
                await self._loop.run_in_executor(None, device.connect, host)
            except Exception as exc:
                _LOG.warning("dyson connect failed (%s) — retry in %ss", exc, _RECONNECT_DELAY)
                # If the cached IP failed, drop it so the next iteration
                # re-resolves via mDNS (device may have moved on the LAN).
                self._cached_ip = None
                await asyncio.sleep(_RECONNECT_DELAY)
                continue
            self._device = device
            self._connected = True
            _LOG.info("connected to %s at %s", self.serial, host)

            # Ask the fan to push its full state immediately so the UCR3
            # tiles paint with real values right away instead of waiting
            # for the device's next periodic push.
            try:
                if hasattr(device, "request_current_status"):
                    await self._loop.run_in_executor(None, device.request_current_status)
                if hasattr(device, "request_environmental_data"):
                    await self._loop.run_in_executor(None, device.request_environmental_data)
            except Exception as exc:
                _LOG.debug("eager state request failed (non-fatal): %s", exc)

            # Notify entities so they refresh from the now-populated state.
            self._on_message(None)

            # Block until disconnect/error.
            while not self._stopped:
                # libdyson maintains its own thread; we just keep the task alive
                # and let on_message do the work. Poll for disconnect.
                if not getattr(device, "is_connected", True):
                    break
                await asyncio.sleep(_ALIVE_POLL)

            self._connected = False
            try:
                device.disconnect()
            except Exception:
                pass
            if not self._stopped:
                _LOG.info("dyson disconnected, reconnecting in %ss", _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)
