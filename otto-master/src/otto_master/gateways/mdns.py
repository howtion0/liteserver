"""mDNS publication for the local Otto Master control plane."""

from __future__ import annotations

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

    async def unregister(self, info: ServiceInfo) -> None: ...

    async def close(self) -> None: ...


class ZeroconfBackend:
    """Small adapter that keeps zeroconf implementation types at the gateway edge."""

    def __init__(self) -> None:
        self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)

    async def register(self, info: ServiceInfo) -> None:
        await self._zeroconf.async_register_service(info, allow_name_change=False)

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
    """Publish and withdraw a single `_otto-master._tcp` service record."""

    def __init__(
        self,
        config: DiscoveryConfig,
        *,
        http_port: int,
        mqtt_port: int,
        project_version: str,
        backend_factory: Callable[[], MdnsBackend] = ZeroconfBackend,
        addresses: tuple[str, ...] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.http_port = http_port
        self.mqtt_port = mqtt_port
        self.project_version = project_version
        self._backend_factory = backend_factory
        self._addresses = addresses or local_ipv4_addresses()
        self._backend: MdnsBackend | None = None
        self._service_info: ServiceInfo | None = None
        self._state = MdnsState.CREATED if config.enabled else MdnsState.DISABLED
        self._last_error: str | None = None
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
        service_type = _fqdn(self.config.service_type)
        hostname = _fqdn(self.config.hostname)
        info = ServiceInfo(
            service_type,
            f"Otto Master.{service_type}",
            addresses=[socket.inet_aton(address) for address in self._addresses],
            port=self.http_port,
            properties={
                "version": self.project_version,
                "http_port": str(self.http_port),
                "mqtt_port": str(self.mqtt_port),
                "api": "/api/v1",
                "ota": "/api/v1/ota/manifest",
            },
            server=hostname,
        )
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
        self._logger.info(
            "mdns_registered",
            extra={
                "event": "mdns_registered",
                "hostname": hostname,
                "service_type": service_type,
                "addresses": list(self._addresses),
            },
        )

    async def shutdown(self) -> None:
        if self._state in {MdnsState.DISABLED, MdnsState.STOPPED}:
            return
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
                extra={"event": "mdns_unregister_failed", "error_type": type(exc).__name__},
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

    def status(self) -> dict[str, Any]:
        enabled = self.config.enabled
        return {
            "enabled": enabled,
            "healthy": self.running if enabled else True,
            "state": self._state.value,
            "hostname": self.config.hostname,
            "service_type": self.config.service_type,
            "addresses": list(self._addresses),
            "last_error": self._last_error,
        }
