#!/usr/bin/env python3
"""Run all local services in one terminal session."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
SHUTDOWN_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    module: str
    port_env: str
    default_port: int


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec("api_gateway", "services.api_gateway.app", "API_GATEWAY_PORT", 8000),
    ServiceSpec(
        "conversation_orchestrator",
        "services.conversation_orchestrator.app",
        "CONVERSATION_ORCHESTRATOR_PORT",
        8001,
    ),
    ServiceSpec("agent_runtime", "services.agent_runtime.app", "AGENT_RUNTIME_PORT", 8002),
    ServiceSpec("data_ingestion", "services.data_ingestion.app", "DATA_INGESTION_PORT", 8003),
    ServiceSpec("topic_pipeline", "services.topic_pipeline.app", "TOPIC_PIPELINE_PORT", 8004),
    ServiceSpec("export_service", "services.export_service.app", "EXPORT_SERVICE_PORT", 8005),
    ServiceSpec("scheduler", "services.scheduler.app", "SCHEDULER_PORT", 8006),
)


def port_for(spec: ServiceSpec) -> int:
    return int(os.getenv(spec.port_env, str(spec.default_port)))


def build_command(spec: ServiceSpec, reload_enabled: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        f"{spec.module}:app",
        "--host",
        os.getenv("SERVICE_HOST", DEFAULT_HOST),
        "--port",
        str(port_for(spec)),
    ]
    if reload_enabled:
        command.append("--reload")
    return command


def start_processes(reload_enabled: bool) -> list[tuple[ServiceSpec, subprocess.Popen]]:
    processes: list[tuple[ServiceSpec, subprocess.Popen]] = []
    host = os.getenv("SERVICE_HOST", DEFAULT_HOST)
    for spec in SERVICES:
        command = build_command(spec, reload_enabled=reload_enabled)
        print(f"[dev] starting {spec.name:<28} http://{host}:{port_for(spec)}")
        process = subprocess.Popen(command, env=os.environ.copy())
        processes.append((spec, process))
    return processes


def stop_processes(processes: list[tuple[ServiceSpec, subprocess.Popen]]) -> None:
    for _spec, process in processes:
        if process.poll() is None:
            process.terminate()

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    for _spec, process in processes:
        if process.poll() is not None:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass

    for spec, process in processes:
        if process.poll() is None:
            print(f"[dev] force-stopping {spec.name}")
            process.kill()


def run(reload_enabled: bool = True) -> int:
    processes = start_processes(reload_enabled=reload_enabled)
    should_stop = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal should_stop
        should_stop = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    exit_code = 0
    try:
        while not should_stop:
            for spec, process in processes:
                process_return_code = process.poll()
                if process_return_code is None:
                    continue
                print(
                    f"[dev] {spec.name} exited with code "
                    f"{process_return_code}; shutting everything down"
                )
                exit_code = process_return_code
                should_stop = True
                break
            time.sleep(0.2)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        stop_processes(processes)
    return exit_code


if __name__ == "__main__":
    no_reload = "--no-reload" in sys.argv
    sys.exit(run(reload_enabled=not no_reload))
