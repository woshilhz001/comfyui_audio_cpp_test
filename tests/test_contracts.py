"""契约层单元测试:验证 model_contracts.py 与 nodes/task.py 的下拉渲染。"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFYUI_ROOT = os.path.dirname(os.path.dirname(PROJECT_ROOT))  # custom_nodes 的上级

sys.path.insert(0, COMFYUI_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))


class TestContracts(unittest.TestCase):
    """model_contracts.py 契约层核心行为。"""

    def test_lifts(self):
        from comfyui_audio_cpp_test.model_contracts import family_lifts

        self.assertEqual(family_lifts("yue2"), {"style", "lyrics"})
        self.assertEqual(family_lifts("heartmula"), {"lyrics"})
        self.assertEqual(family_lifts("seed_vc"), set())

    def test_required_inputs(self):
        from comfyui_audio_cpp_test.model_contracts import family_required_inputs

        self.assertEqual(family_required_inputs("yue2"), {"text", "style"})
        self.assertEqual(family_required_inputs("sheetsage2"), {"audio"})
        self.assertEqual(family_required_inputs("muscriptor_small"), {"audio"})
        self.assertEqual(family_required_inputs("seed_vc"), {"audio", "voice_ref"})
        self.assertEqual(family_required_inputs("mel_band_roformer"), {"audio"})
        self.assertEqual(family_required_inputs("controlfoley"), set())
        self.assertEqual(family_required_inputs("heartmula"), {"text"})

    def test_lifted_value(self):
        from comfyui_audio_cpp_test.model_contracts import lifted_value

        self.assertEqual(lifted_value("lyrics", "  My lyrics ", ""), "My lyrics")
        self.assertEqual(lifted_value("style", "", ""), None)

    def test_new_families_present(self):
        from comfyui_audio_cpp_test import model_profiles

        task_fams = model_profiles.all_family_options("task")
        for family in ("yue2", "sheetsage2", "rvc", "heartmula", "audiosr", "controlfoley"):
            self.assertIn(family, task_fams)

    def test_dropdown_hides_lifted_fields(self):
        from comfyui_audio_cpp_test.nodes.task import _task_options

        opts = {o.key: [i.id for i in o.inputs] for o in _task_options()}
        self.assertIn("yue2", opts)
        self.assertNotIn("style", opts["yue2"])
        self.assertNotIn("lyrics", opts["yue2"])
        self.assertEqual(opts["sheetsage2"], ["max_tokens"])

    def test_muscriptor_no_duplicate_family(self):
        """muscriptor_small(id)覆盖了 spec family muscriptor,下拉不应有重复。"""
        from comfyui_audio_cpp_test import model_profiles

        task_fams = model_profiles.all_family_options("task")
        self.assertIn("muscriptor_small", task_fams)
        self.assertNotIn("muscriptor", task_fams)

    def test_prefix_family_not_swallowed(self):
        """chatterbox 与 chatterbox_turbo 是两个独立家族,前缀启发式不应误吞。"""
        from comfyui_audio_cpp_test import model_profiles

        tts_fams = model_profiles.all_family_options("tts")
        # chatterbox_turbo 是手工档案;chatterbox 是独立 spec 家族,应自动发现。
        self.assertIn("chatterbox_turbo", tts_fams)
        self.assertIn("chatterbox", tts_fams)

    def test_option_value_filter_drops_zero_and_negative_seed(self):
        """guidance_scale 0.0 / int 0 / 负 seed 不进 options(服务器会 500)。"""
        from comfyui_audio_cpp_test.audio_cpp_client import is_meaningful_option_value

        self.assertFalse(is_meaningful_option_value("guidance_scale", 0.0))
        self.assertFalse(is_meaningful_option_value("guidance_scale", 0))
        self.assertTrue(is_meaningful_option_value("guidance_scale", 2.0))
        self.assertFalse(is_meaningful_option_value("text_chunk_size", 0))
        self.assertTrue(is_meaningful_option_value("text_chunk_size", 400))
        self.assertFalse(is_meaningful_option_value("seed", -1))
        self.assertTrue(is_meaningful_option_value("seed", 42))
        self.assertFalse(is_meaningful_option_value("instruct", ""))
        self.assertTrue(is_meaningful_option_value("instruct", "温暖"))
        self.assertTrue(is_meaningful_option_value("f0_condition", False))  # bool False 是有效值

    def test_asr_models_autodiscovered(self):
        """ASR 节点模型下拉应包含自动发现的 spec 家族。"""
        from comfyui_audio_cpp_test.nodes.asr import _asr_model_options

        models = _asr_model_options()
        self.assertIn("qwen3_asr", models)
        self.assertGreater(len(models), 1)


if __name__ == "__main__":
    unittest.main()