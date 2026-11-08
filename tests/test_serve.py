"""Служба и доступ: сервер слушает только `127.0.0.1`, порт берётся из настроек."""

from __future__ import annotations

import socket

import pytest


def test_server_listens_on_loopback_at_port_from_settings(spook):
    port = spook.settings.port

    with socket.create_connection(("127.0.0.1", port), timeout=1):
        pass
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.2", port), timeout=1)


def network_address() -> str | None:
    """Адрес машины в сети — тот, с которого она ходит наружу; None, если сети нет."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # UDP: выбирается маршрут, пакеты не уходят
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def test_server_does_not_accept_connections_on_network_address_of_machine(spook):
    address = network_address()
    if address is None:
        pytest.skip("у машины нет сетевого адреса")

    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((address, spook.settings.port), timeout=1)
