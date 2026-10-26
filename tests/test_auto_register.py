"""ensure_model_registered 单测:mock HTTP,覆盖已注册/未注册/403 三条路径。"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFYUI_ROOT = os.path.dirname(os.path.dirname(PROJECT_ROOT))  # custom_nodes 的上级

sys.path.insert(0, COMFYUI_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))


def _response(status: int, payload: dict | None = None, text: str = ""):
    resp = mock.Mock()
    resp.status_code = status
    if payload is not None:
        resp.json.return_value = payload
    else:
        resp.json.side_effect = ValueError("not json")
    resp.text = text or json.dumps(payload, ensure_ascii=False)
    return resp


def _patch_requests(get_resp, post_resp=None):
    """同时 patch requests.get / requests.post,返回 (get_mock, post_mock)。"""
    get_m = mock.Mock(return_value=get_resp)
    post_m = mock.Mock(return_value=post_resp)
    return get_m, post_m, mock.patch.multiple(
        "requests", get=get_m, post=post_m
    )


class TestEnsureModelRegistered(unittest.TestCase):
    """audio_cpp_client.ensure_model_registered 的三条主要路径。"""

    def test_already_registered_returns_id_without_post(self):
        from comfyui_audio_cpp_test.audio_cpp_client import ensure_model_registered

        get_m, post_m, patcher = _patch_requests(
            _response(200, {"data": [{"id": "qwen3_tts", "loaded": False}]})
        )
        with patcher:
            result = ensure_model_registered("http://127.0.0.1:8085", "qwen3_tts")
        self.assertEqual(result, "qwen3_tts")
        post_m.assert_not_called()

    def test_not_registered_triggers_load_with_spec_task(self):
        from comfyui_audio_cpp_test.audio_cpp_client import ensure_model_registered

        # 未注册家族:第一次 GET 无条目,load 后复查 GET 有条目。
        empty = _response(200, {"data": []})
        listed = _response(200, {"data": [{"id": "no_such_family_xyz", "loaded": True}]})
        load_resp = _response(200, {"id": "no_such_family_xyz", "loaded": True, "reconfigured": False})
        get_m, post_m, patcher = _patch_requests(empty, load_resp)
        get_m.side_effect = [empty, listed]
        with patcher:
            result = ensure_model_registered(
                "http://127.0.0.1:8085", "no_such_family_xyz", path="models/Foo-GGUF"
            )
        self.assertEqual(result, "no_such_family_xyz")
        post_m.assert_called_once()
        args, kwargs = post_m.call_args
        self.assertEqual(args[0], "http://127.0.0.1:8085/v1/models/load")
        payload = kwargs["json"]
        self.assertEqual(payload["id"], "no_such_family_xyz")
        self.assertEqual(payload["family"], "no_such_family_xyz")
        self.assertEqual(payload["path"], "models/Foo-GGUF")
        self.assertEqual(payload["mode"], "offline")
        self.assertIn("task", payload)

    def test_default_path_is_models_family(self):
        from comfyui_audio_cpp_test.audio_cpp_client import default_model_path

        self.assertEqual(default_model_path("fireredtts3"), "models/fireredtts3")

    def test_spec_tasks_used_for_real_family(self):
        from comfyui_audio_cpp_test.audio_cpp_client import ensure_model_registered

        # fireredtts3 在 model_specs 中存在,tasks=["tts","clone","design"],
        # 未注册时 load payload 的 task 应取首个任务 "tts"。
        if not __import__("comfyui_audio_cpp_test.model_profiles", fromlist=["spec_families"]).spec_families().get("fireredtts3"):
            self.skipTest("本机 model_specs 缺少 fireredtts3.json")
        empty = _response(200, {"data": []})
        listed = _response(200, {"data": [{"id": "fireredtts3", "loaded": True}]})
        load_resp = _response(200, {"id": "fireredtts3", "loaded": True, "reconfigured": False})
        get_m, post_m, patcher = _patch_requests(empty, load_resp)
        get_m.side_effect = [empty, listed]
        with patcher:
            result = ensure_model_registered("http://127.0.0.1:8085", "fireredtts3")
        self.assertEqual(result, "fireredtts3")
        payload = post_m.call_args.kwargs["json"]
        self.assertEqual(payload["task"], "tts")
        self.assertEqual(payload["family"], "fireredtts3")
        # 路径按家族名在 models/ 下智能定位(WebUI 下载目录名带后缀,
        # 如 FireRedTTS3-Instruct-GGUF),只校验前缀与包含家族名。
        self.assertTrue(payload["path"].startswith("models/"))
        self.assertIn("fireredtts3", payload["path"].lower())

    def test_403_reports_ui_management_disabled(self):
        from comfyui_audio_cpp_test.audio_cpp_client import (
            AudioCppError,
            ensure_model_registered,
        )

        empty = _response(200, {"data": []})
        forbidden = _response(403, {"error": "dynamic model management is disabled"})
        get_m, post_m, patcher = _patch_requests(empty, forbidden)
        with patcher:
            with self.assertRaises(AudioCppError) as ctx:
                # 显式传 path 绕过本地目录探测,直达 load 的 403 分支。
                ensure_model_registered(
                    "http://127.0.0.1:8085", "no_such_family_xyz", path="models/NoSuch"
                )
        self.assertIn("--ui-management", str(ctx.exception))


class TestPickModelDir(unittest.TestCase):
    """_pick_model_dir 的目录定位:下划线 family vs 连字符目录名。"""

    def _root_with(self, *dirs):
        import tempfile

        root = Path(tempfile.mkdtemp())
        for d in dirs:
            (root / d).mkdir()
            (root / d / "model.gguf").write_bytes(b"x")
        return root

    def test_breeze_tts_picks_hyphenated_dir(self):
        from comfyui_audio_cpp_test.audio_cpp_client import _pick_model_dir

        root = self._root_with("Breeze-TTS-2-GGUF", "Chatterbox-Turbo-GGUF")
        picked = _pick_model_dir(root, "breeze_tts")
        self.assertEqual(picked.name, "Breeze-TTS-2-GGUF")

    def test_seed_vc_picks_abbreviation_dir(self):
        from comfyui_audio_cpp_test.audio_cpp_client import _pick_model_dir

        # seed_vc 是缩写写法,靠去分隔符后包含命中 SeedVC-MLX-GGUF。
        root = self._root_with("SeedVC-MLX-GGUF", "Sortformer-Diar-4spk-v1-GGUF")
        picked = _pick_model_dir(root, "seed_vc")
        self.assertEqual(picked.name, "SeedVC-MLX-GGUF")

    def test_exact_and_prefix_still_preferred(self):
        from comfyui_audio_cpp_test.audio_cpp_client import _pick_model_dir

        # 完全相等 > family-gguf > 前缀 > 包含 > 去分隔符包含:
        # 同名族存在多个候选时,前缀目录优先于仅缩写匹配的目录。
        root = self._root_with("FireRedTTS3-Instruct-GGUF", "FireRed-Audio-GGUF")
        picked = _pick_model_dir(root, "fireredtts3")
        self.assertEqual(picked.name, "FireRedTTS3-Instruct-GGUF")

    def test_no_match_returns_none(self):
        from comfyui_audio_cpp_test.audio_cpp_client import _pick_model_dir

        root = self._root_with("CosyVoice3-GGUF")
        self.assertIsNone(_pick_model_dir(root, "no_such_family_xyz"))
        self.assertIsNone(_pick_model_dir(root / "missing", "cosyvoice3"))


if __name__ == "__main__":
    unittest.main()
