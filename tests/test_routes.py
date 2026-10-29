"""routes.unload_models_handler 单测:请求体校验、代理转发与错误映射。

用假 request(只实现 json())驱动,不发真实网络请求:
- 非法 mode / 空 model_ids → 400;
- 服务器不可达(AudioCppError)→ 502;
- 正常卸载 → 200 {"ok": true, "unloaded": [...], "report": ...}。
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest import mock

import pytest

import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFYUI_ROOT = os.path.dirname(os.path.dirname(PROJECT_ROOT))  # custom_nodes 的上级
sys.path.insert(0, COMFYUI_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))

# 不触发包 __init__(它 import comfy_api,依赖 av);
# 手工建一个只含 __path__ 的假包,直接加载 routes 子模块。
if "comfyui_audio_cpp_test" not in sys.modules:
    _pkg = types.ModuleType("comfyui_audio_cpp_test")
    _pkg.__path__ = [PROJECT_ROOT]
    sys.modules["comfyui_audio_cpp_test"] = _pkg

from comfyui_audio_cpp_test import routes
from comfyui_audio_cpp_test.audio_cpp_client import AudioCppError


class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _run(request):
    return asyncio.run(routes.unload_models_handler(request))


def _text(resp):
    import json as _json
    return _json.loads(resp.text)


def test_invalid_mode_returns_400():
    resp = _run(_FakeRequest({"mode": "bogus"}))
    assert resp.status == 400
    assert "bogus" in _text(resp)["error"]


def test_unload_selected_without_ids_returns_400():
    resp = _run(_FakeRequest({"mode": "unload_selected", "model_ids": "  "}))
    assert resp.status == 400


def test_server_down_returns_502():
    with mock.patch.object(routes, "check_server", side_effect=AudioCppError("连不上服务器")):
        resp = _run(_FakeRequest({}))
    assert resp.status == 502
    assert "连不上服务器" in _text(resp)["error"]


def test_unload_all_success():
    with (
        mock.patch.object(routes, "check_server", return_value={}),
        mock.patch.object(routes, "list_models", return_value=[
            {"id": "qwen3_tts", "loaded": True},
            {"id": "qwen3_asr", "loaded": True},
        ]),
        mock.patch.object(routes, "unload_all_models", return_value=["qwen3_tts", "qwen3_asr"]),
    ):
        resp = _run(_FakeRequest({"mode": "unload_all"}))
    data = _text(resp)
    assert data["ok"] is True
    assert data["unloaded"] == ["qwen3_tts", "qwen3_asr"]


def test_unload_selected_success():
    with (
        mock.patch.object(routes, "check_server", return_value={}),
        mock.patch.object(routes, "list_models", return_value=[
            {"id": "qwen3_tts", "loaded": True},
        ]),
        mock.patch.object(routes, "unload_models", return_value=["qwen3_tts"]),
    ):
        resp = _run(_FakeRequest({"mode": "unload_selected", "model_ids": "qwen3_tts, qwen3_asr"}))
    data = _text(resp)
    assert data["ok"] is True
    assert data["unloaded"] == ["qwen3_tts"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
