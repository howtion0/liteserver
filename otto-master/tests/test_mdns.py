from __future__ import annotations

import asyncio

from zeroconf import ServiceInfo

from otto_master.config import DiscoveryConfig
from otto_master.gateways.mdns import MdnsGateway, MdnsState


class FakeMdnsBackend:
    def __init__(
        self,
        *,
        fail_registration: bool = False,
        fail_update: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.fail_registration = fail_registration
        self.fail_update = fail_update
        self.fail_close = fail_close
        self.registered: list[ServiceInfo] = []
        self.updated: list[ServiceInfo] = []
        self.unregistered: list[ServiceInfo] = []
        self.closed = False

    async def register(self, info: ServiceInfo) -> None:
        if self.fail_registration:
            raise OSError("multicast unavailable")
        self.registered.append(info)

    async def update(self, info: ServiceInfo) -> None:
        if self.fail_update:
            raise OSError("multicast update unavailable")
        self.updated.append(info)

    async def unregister(self, info: ServiceInfo) -> None:
        self.unregistered.append(info)

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise OSError("close unavailable")


def _config(
    *,
    enabled: bool = True,
    refresh_interval_seconds: float = 5,
) -> DiscoveryConfig:
    return DiscoveryConfig(
        enabled=enabled,
        hostname="master.local",
        service_type="_otto-master._tcp.local.",
        refresh_interval_seconds=refresh_interval_seconds,
    )


async def test_mdns_registers_expected_service_and_unregisters() -> None:
    backend = FakeMdnsBackend()
    gateway = MdnsGateway(
        _config(),
        http_port=8080,
        mqtt_port=1883,
        project_version="0.3.0",
        backend_factory=lambda: backend,
        addresses=("192.0.2.10",),
    )

    await gateway.start()

    assert gateway.state is MdnsState.RUNNING
    assert len(backend.registered) == 1
    info = backend.registered[0]
    assert info.type == "_otto-master._tcp.local."
    assert info.name == "Otto Master._otto-master._tcp.local."
    assert info.server == "master.local."
    assert info.port == 8080
    assert info.parsed_addresses() == ["192.0.2.10"]
    assert info.decoded_properties["mqtt_port"] == "1883"
    assert info.decoded_properties["ota"] == "/api/v1/ota/manifest"

    await gateway.shutdown()

    assert gateway.state is MdnsState.STOPPED
    assert backend.unregistered == [info]
    assert backend.closed is True
    assert gateway.status()["monitor_running"] is False


async def test_disabled_mdns_does_not_create_backend() -> None:
    created = False

    def backend_factory() -> FakeMdnsBackend:
        nonlocal created
        created = True
        return FakeMdnsBackend()

    gateway = MdnsGateway(
        _config(enabled=False),
        http_port=8080,
        mqtt_port=1883,
        project_version="0.3.0",
        backend_factory=backend_factory,
        addresses=("127.0.0.1",),
    )

    await gateway.start()
    await gateway.shutdown()

    assert created is False
    assert gateway.state is MdnsState.DISABLED
    assert gateway.status()["healthy"] is True


async def test_mdns_shutdown_finishes_when_backend_close_fails() -> None:
    backend = FakeMdnsBackend(fail_close=True)
    gateway = MdnsGateway(
        _config(),
        http_port=8080,
        mqtt_port=1883,
        project_version="0.3.0",
        backend_factory=lambda: backend,
        addresses=("192.0.2.10",),
    )
    await gateway.start()

    await gateway.shutdown()

    assert gateway.state is MdnsState.STOPPED
    assert gateway.status()["last_error"] == "OSError: close unavailable"


async def test_mdns_monitor_updates_service_when_lan_address_changes() -> None:
    backend = FakeMdnsBackend()
    current_addresses = [("192.0.2.10",)]
    gateway = MdnsGateway(
        _config(refresh_interval_seconds=0.01),
        http_port=8081,
        mqtt_port=1883,
        project_version="0.5.0",
        backend_factory=lambda: backend,
        address_provider=lambda: current_addresses[0],
    )

    await gateway.start()
    current_addresses[0] = ("192.0.2.11",)
    deadline = asyncio.get_running_loop().time() + 1
    while not backend.updated and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)

    assert len(backend.updated) == 1
    refreshed = backend.updated[0]
    assert refreshed.parsed_addresses() == ["192.0.2.11"]
    status = gateway.status()
    assert status["addresses"] == ["192.0.2.11"]
    assert status["refresh_count"] == 1
    assert status["refresh_failures"] == 0
    assert status["monitor_running"] is True

    await gateway.shutdown()

    assert backend.unregistered == [refreshed]


async def test_mdns_keeps_last_lan_address_during_transient_loopback_only_state() -> None:
    backend = FakeMdnsBackend()
    current_addresses = [("192.0.2.10",)]
    gateway = MdnsGateway(
        _config(),
        http_port=8081,
        mqtt_port=1883,
        project_version="0.5.0",
        backend_factory=lambda: backend,
        address_provider=lambda: current_addresses[0],
    )
    await gateway.start()
    current_addresses[0] = ("127.0.0.1",)

    assert await gateway.refresh_addresses() is False
    assert gateway.status()["addresses"] == ["192.0.2.10"]
    assert backend.updated == []

    await gateway.shutdown()


async def test_mdns_refresh_failure_keeps_old_record_and_retries() -> None:
    backend = FakeMdnsBackend(fail_update=True)
    current_addresses = [("192.0.2.10",)]
    gateway = MdnsGateway(
        _config(),
        http_port=8081,
        mqtt_port=1883,
        project_version="0.5.0",
        backend_factory=lambda: backend,
        address_provider=lambda: current_addresses[0],
    )
    await gateway.start()
    current_addresses[0] = ("192.0.2.11",)

    assert await gateway.refresh_addresses() is False
    failed = gateway.status()
    assert failed["addresses"] == ["192.0.2.10"]
    assert failed["healthy"] is False
    assert failed["refresh_failures"] == 1

    backend.fail_update = False
    assert await gateway.refresh_addresses() is True
    recovered = gateway.status()
    assert recovered["addresses"] == ["192.0.2.11"]
    assert recovered["healthy"] is True
    assert recovered["refresh_count"] == 1

    await gateway.shutdown()
