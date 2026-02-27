"""Tests for local development launcher config."""

from scripts.dev import SERVICES, ServiceSpec, build_command


def test_service_specs_are_unique() -> None:
    names = [spec.name for spec in SERVICES]
    ports = [spec.default_port for spec in SERVICES]
    assert len(names) == 7
    assert len(set(names)) == len(names)
    assert len(set(ports)) == len(ports)


def test_build_command_targets_uvicorn() -> None:
    spec = ServiceSpec(
        name="example",
        module="services.api_gateway.app",
        port_env="EXAMPLE_PORT",
        default_port=9999,
    )
    command = build_command(spec, reload_enabled=False)
    assert command[1:4] == ["-m", "uvicorn", "services.api_gateway.app:app"]
    assert "--reload" not in command
