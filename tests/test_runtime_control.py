from __future__ import annotations

from readio import runtime


def test_runtime_status_delegates_to_voicerender_root(monkeypatch) -> None:
    status = object()
    calls = []

    def get_status():
        calls.append("status")
        return status

    monkeypatch.setattr(runtime.voicerender, "onnx_runtime_status", get_status)

    assert runtime.runtime_status() is status
    assert calls == ["status"]


def test_set_runtime_providers_delegates_without_reinterpreting(monkeypatch) -> None:
    providers = ("cuda", "cpu")
    calls = []

    def set_providers(value):
        calls.append(value)

    monkeypatch.setattr(runtime.voicerender, "set_onnx_runtime_providers", set_providers)

    assert runtime.set_runtime_providers(providers) is None
    assert calls == [providers]


def test_reset_runtime_providers_clears_then_returns_current_status(monkeypatch) -> None:
    status = object()
    calls = []

    def clear_providers():
        calls.append("clear")

    def get_status():
        calls.append("status")
        return status

    monkeypatch.setattr(runtime.voicerender, "clear_onnx_runtime_providers", clear_providers)
    monkeypatch.setattr(runtime.voicerender, "onnx_runtime_status", get_status)

    assert runtime.reset_runtime_providers() is status
    assert calls == ["clear", "status"]
