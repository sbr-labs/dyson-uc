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
import threading
from typing import Awaitable, Callable

from libdyson import get_device
from libdyson.discovery import DysonDiscovery
from zeroconf import Zeroconf

_LOG = logging.getLogger(__name__)

_RECONNECT_DELAY = 3.0
_ALIVE_POLL = 1.0
# How long to wait for a multicast mDNS reply before giving up and trying
# the OS resolver. Dyson fans answer in well under a second on a healthy
# LAN; 6s covers a sleepy fan / slow Wi-Fi without stalling reconnects.
_MDNS_TIMEOUT = 6.0


class DysonClient:
    def __init__(
        self,
        serial: str,
        credential: str,
        product_type: str,
        on_state_change: Callable[[], Awaitable[None] | None],
        loop: asyncio.AbstractEventLoop | None = None,
        static_ip: str | None = None,
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
        # First-state-after-connect flag — flipped to False once the driver
        # has done its UCR3 tile-cache blink on the initial connect. Stops
        # the blink from re-firing on every MQTT reconnect after that.
        self.first_connect_pending = True
        # User-supplied LAN IP from setup config. If set, we skip mDNS
        # entirely and always connect directly to this address. Used when
        # the user's network doesn't resolve the device's .local hostname
        # (some mesh routers, VLAN segregation, etc.).
        self._static_ip = static_ip
        # Cache the IP across reconnect cycles so we skip mDNS resolution
        # on every retry — mDNS lookup is 500ms-2s and the IP rarely
        # changes mid-session.
        self._cached_ip: str | None = static_ip

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

    def _resolve_via_mdns(self, timeout: float = _MDNS_TIMEOUT) -> str | None:
        """Resolve the device's LAN IP with a true multicast mDNS query.

        We must NOT use socket.gethostbyname() on the "<serial>.local" name:
        that routes through the host OS resolver, which only understands
        .local when Avahi/nss-mdns is wired into nsswitch.conf. The UCR3
        integration sandbox has no such plumbing (firmware 2.9.4 removed
        whatever made it resolve before — getaddrinfo now returns EAI_AGAIN,
        "Temporary failure in name resolution").

        zeroconf does the multicast query (224.0.0.251:5353) in-process, so
        it resolves the fan regardless of the host OS resolver. We reuse
        libdyson's own DysonDiscovery so the service type / serial parsing
        stay in lockstep with the library.
        """
        result: dict[str, str] = {}
        found = threading.Event()

        # DysonDiscovery only reads `.serial` off the registered device.
        stub = type("_Dev", (), {"serial": self.serial})()

        zc = Zeroconf()
        discovery = DysonDiscovery()
        try:
            discovery.start_discovery(zc)
            discovery.register_device(stub, lambda addr: (result.__setitem__("ip", addr), found.set()))
            if not found.wait(timeout):
                return None
            return result.get("ip")
        finally:
            try:
                discovery.stop_discovery()
            except Exception:
                pass
            try:
                zc.close()
            except Exception:
                pass

    def _resolve_host(self) -> str | None:
        # Static IP from setup always wins — never re-resolve via mDNS.
        if self._static_ip:
            return self._static_ip
        # Try the cached IP first — mDNS discovery is unnecessary if the
        # device hasn't moved. We only re-resolve on cold start or after a
        # cached-IP connect fails.
        if self._cached_ip:
            return self._cached_ip
        # Primary path: real multicast mDNS via zeroconf. Works inside the
        # UCR3 sandbox where the OS resolver can't see .local names.
        ip = self._resolve_via_mdns()
        if ip:
            self._cached_ip = ip
            return ip
        # Last-ditch fallback: the OS resolver. Harmless where .local isn't
        # plumbed (just fails); succeeds on hosts/networks that do support
        # it, so we keep it rather than hard-failing.
        hostname = f"{self.serial.lower()}.local"
        try:
            ip = socket.gethostbyname(hostname)
            self._cached_ip = ip
            return ip
        except OSError as exc:
            _LOG.warning(
                "mDNS resolve failed for %s (multicast timeout + OS resolver "
                "%s) — set a Static LAN IP in the integration setup if your "
                "network blocks mDNS (VLANs / AP isolation / mesh)",
                hostname, exc,
            )
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
            # Run in an executor: mDNS discovery blocks up to _MDNS_TIMEOUT
            # waiting on the multicast reply, and must not stall the loop.
            host = await self._loop.run_in_executor(None, self._resolve_host)
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
                # The static IP from setup is NEVER cleared — we trust the
                # user's override and retry the same address forever.
                if not self._static_ip:
                    self._cached_ip = None
                await asyncio.sleep(_RECONNECT_DELAY)
                continue
            self._device = device
            self._connected = True
            _LOG.info("connected to %s at %s (port 1883 MQTT)", self.serial, host)

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
                # Loud log — a flapping MQTT connection is the most common
                # bug report cause, and seeing this repeat in the logs every
                # 3 seconds is the signal to look for a competing client
                # (Dyson app, ha-dyson, etc.) on the same fan.
                _LOG.warning(
                    "MQTT disconnected from %s, reconnecting in %ss "
                    "(if this repeats, another client may be holding the fan's session)",
                    self.serial, _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)
