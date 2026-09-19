"""mDNS publication for the local Otto Master control plane."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from ..config import DiscoveryConfig


class MdnsError(RuntimeError):
    """Raised when the service cannot be published or withdrawn."""


class MdnsState(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"


class MdnsBackend(Protocol):
    async def register(self, info: ServiceInfo) -> None: ...

    async def update(self, info: ServiceInfo) -> None: ...

    async def unregister(self, info: ServiceInfo) -> None: ...

    async def close(self) -> None: ...


class ZeroconfBackend:
    """Small adapter that keeps zeroconf implementation types at the gateway edge."""

    def __init__(self) -> None:
        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)

    async def register(self, info: ServiceInfo) -> None:
        await self._zeroconf.async_register_service(info, allow_name_change=False)

    async def update(self, info: ServiceInfo) -> None:
        await self._zeroconf.async_update_service(info)

    async def unregister(self, info: ServiceInfo) -> None:
        await self._zeroconf.async_unregister_service(info)

    async def close(self) -> None:
        await self._zeroconf.async_close()


def local_ipv4_addresses() -> tuple[str, ...]:
    """Return usable host addresses without depending on a fixed interface name."""

    addresses: set[str] = set()
    try:
        records = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
    except OSError:
        records = []
    for record in records:
        value = str(record[4][0])
        if value != "0.0.0.0":
            addresses.add(value)
    non_loopback = sorted(address for address in addresses if not address.startswith("127."))
    if non_loopback:
        return tuple(non_loopback)
    return ("127.0.0.1",)


def _fqdn(value: str) -> str:
    return value if value.endswith(".") else f"{value}."


class MdnsGateway:
    """Publish one `_otto-master._tcp` record and follow host address changes."""

    def __init__(
        self,
        config: DiscoveryConfig,
        *,
        http_port: int,
        mqtt_port: int,
        project_version: str,
        backend_factory: Callable[[], MdnsBackend] = ZeroconfBackend,
        addresses: tuple[str, ...] | None = None,
        address_provider: Callable[[], tuple[str, ...]] = local_ipv4_addresses,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.http_port = http_port
        self.mqtt_port = mqtt_port
        self.project_version = project_version
        self._backend_factory = backend_factory
        if addresses is None:
            self._address_provider = address_provider
            self._addresses = _normalized_addresses(address_provider())
        else:
            static_addresses = _normalized_addresses(addresses)
            self._address_provider = lambda: static_addresses
            self._addresses = static_addresses
        self._backend: MdnsBackend | None = None
        self._service_info: ServiceInfo | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._state = MdnsState.CREATED if config.enabled else MdnsState.DISABLED
        self._last_error: str | None = None
        self._refresh_count = 0
        self._refresh_failures = 0
        self._logger = logger or logging.getLogger("otto_master.mdns")

    @property
    def state(self) -> MdnsState:
        return self._state

    @property
    def running(self) -> bool:
        return self._state is MdnsState.RUNNING

    @property
    def service_info(self) -> ServiceInfo | None:
        return self._service_info

    async def start(self) -> None:
        if self._state is MdnsState.DISABLED or self.running:
            return
        async with self._operation_lock:
            self._addresses = _normalized_addresses(self._address_provider())
            service_type = _fqdn(self.config.service_type)
            hostname = _fqdn(self.config.hostname)
            info = self._build_service_info(self._addresses)
            backend = self._backend_factory()
            self._backend = backend
            self._service_info = info
            try:
                await backend.register(info)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._state = MdnsState.ERROR
                try:
                    await backend.close()
                finally:
                    self._backend = None
                raise MdnsError(f"failed to register mDNS hostname {hostname}") from exc
            self._state = MdnsState.RUNNING
            self._last_error = None
            self._monitor_task = asyncio.create_task(
                self._monitor_addresses(),
                name="otto-mdns-address-monitor",
            )
        self._logger.info(
            "mdns_registered",
            extra={
                "event": "mdns_registered",
                "hostname": hostname,
                "service_type": service_type,
                "addresses": list(self._addresses),
            },
        )

    async def refresh_addresses(self) -> bool:
        """Update the published A records when the active LAN address changes."""

        if not self.running:
            return False
        try:
            candidate = _normalized_addresses(self._address_provider())
        except Exception as exc:  # noqa: BLE001 - retain the last valid publication
            self._record_refresh_failure(exc)
            return False
        if _only_loopback(candidate) and not _only_loopback(self._addresses):
            # During a Wi-Fi handoff macOS may briefly report only loopback.
            # Keep the last LAN record until a usable replacement appears.
            return False
        async with self._operation_lock:
            if not self.running or candidate == self._addresses:
                if self.running:
                    self._last_error = None
                return False
            backend = self._backend
            if backend is None:
                return False
            info = self._build_service_info(candidate)
            previous = self._addresses
            try:
                await backend.update(info)
            except Exception as exc:  # noqa: BLE001 - monitor retries on the next interval
                self._record_refresh_failure(exc)
                return False
            self._addresses = candidate
            self._service_info = info
            self._refresh_count += 1
            self._last_error = None
        self._logger.info(
            "mdns_addresses_refreshed",
            extra={
                "event": "mdns_addresses_refreshed",
                "previous_addresses": list(previous),
                "addresses": list(candidate),
            },
        )
        return True

    async def shutdown(self) -> None:
        if self._state in {MdnsState.DISABLED, MdnsState.STOPPED}:
            return
        monitor_task = self._monitor_task
        self._monitor_task = None
        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        async with self._operation_lock:
            backend = self._backend
            info = self._service_info
            self._backend = None
            try:
                if backend is not None and info is not None and self.running:
                    await backend.unregister(info)
            except Exception as exc:  # noqa: BLE001 - shutdown must still close the backend
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._logger.warning(
                    "mdns_unregister_failed",
                    extra={
                        "event": "mdns_unregister_failed",
                        "error_type": type(exc).__name__,
                    },
                )
            try:
                if backend is not None:
                    await backend.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must finish after backend faults
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._logger.warning(
                    "mdns_close_failed",
                    extra={"event": "mdns_close_failed", "error_type": type(exc).__name__},
                )
            finally:
                self._state = MdnsState.STOPPED
                self._logger.info("mdns_unregistered", extra={"event": "mdns_unregistered"})

    async def _monitor_addresses(self) -> None:
        while True:
            await asyncio.sleep(self.config.refresh_interval_seconds)
            await self.refresh_addresses()

    def _build_service_info(self, addresses: tuple[str, ...]) -> ServiceInfo:
        service_type = _fqdn(self.config.service_type)
        return ServiceInfo(
            service_type,
            f"Otto Master.{service_type}",
            addresses=[socket.inet_aton(address) for address in addresses],
            port=self.http_port,
            properties={
                "version": self.project_version,
                "http_port": str(self.http_port),
                "mqtt_port": str(self.mqtt_port),
                "api": "/api/v1",
                "ota": "/api/v1/ota/manifest",
            },
            server=_fqdn(self.config.hostname),
        )

    def _record_refresh_failure(self, exc: Exception) -> None:
        self._refresh_failures += 1
        self._last_error = f"{type(exc).__name__}: {exc}"
        self._logger.warning(
            "mdns_address_refresh_failed",
            extra={
                "event": "mdns_address_refresh_failed",
                "error_type": type(exc).__name__,
            },
        )

    def status(self) -> dict[str, Any]:
        enabled = self.config.enabled
        monitor_running = self._monitor_task is not None and not self._monitor_task.done()
        return {
            "enabled": enabled,
            "healthy": (
                self.running and monitor_running and self._last_error is None
                if enabled
                else True
            ),
            "state": self._state.value,
            "hostname": self.config.hostname,
            "service_type": self.config.service_type,
            "addresses": list(self._addresses),
            "refresh_interval_seconds": self.config.refresh_interval_seconds,
            "refresh_count": self._refresh_count,
            "refresh_failures": self._refresh_failures,
            "monitor_running": monitor_running,
            "last_error": self._last_error,
        }


def _normalized_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(
        sorted(
            {
                str(address).strip()
                for address in addresses
                if str(address).strip()
            }
        )
    )
    if not normalized:
        return ("127.0.0.1",)
    for address in normalized:
        try:
            socket.inet_aton(address)
        except OSError as exc:
            raise ValueError(f"invalid IPv4 address: {address}") from exc
    return normalized


def _only_loopback(addresses: tuple[str, ...]) -> bool:
    return all(address.startswith("127.") for address in addresses)
