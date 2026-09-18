from __future__ import annotations

from zeroconf import ServiceInfo

from otto_master.config import DiscoveryConfig
from otto_master.gateways.mdns import MdnsGateway, MdnsState


class FakeMdnsBackend:
    def __init__(
        self,
        *,
        fail_registration: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.fail_registration = fail_registration
        self.fail_close = fail_close
        self.registered: list[ServiceInfo] = []
        self.unregistered: list[ServiceInfo] = []
        self.closed = False

    async def register(self, info: ServiceInfo) -> None:
        if self.fail_registration:
            raise OSError("multicast unavailable")
        self.registered.append(info)

    async def unregister(self, info: ServiceInfo) -> None:
        self.unregistered.append(info)

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise OSError("close unavailable")


def _config(*, enabled: bool = True) -> DiscoveryConfig:
    return DiscoveryConfig(
        enabled=enabled,
        hostname="master.local",
        service_type="_otto-master._tcp.local.",
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
