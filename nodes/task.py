"""AudioCppAudioTask: 通用音频任务节点(VC / 分离 / 说话人分离 / 音乐 / MIDI)。

通过 audio.cpp 的通用任务端点 POST /v1/tasks/run 工作,该端点接受:
    text        —— 字符串(歌词、提示、音符等)
    audio       —— 服务器端音频路径(源音频,经 /v1/ui/upload 上传)
    voice_ref   —— 服务器端参考音频路径(目标声音,同样上传)
    options     —— 请求选项对象(模型族特定参数)

响应可含 text / audio(base64 WAV) / sample_rate / channels /
named_audio_outputs / words / artifacts / timing。

设计理念:
- 「model」用 DynamicCombo:选模型族只显示该族真实需要的参数,类型严格
  对齐 audio.cpp API(string/int/float/bool/enum/服务器路径)。
- 输出统一 3 档:主音频(AUDIO,可能为空)、文本(STRING)、完整结果 JSON
  (STRING)。JSON 保留 named_audio_outputs(如人声/伴奏两路)、words、timing,
  方便下游或调试。
- 上传音频经 /v1/ui/upload(需要 server 以 --ui / --ui-management 启动)。
"""

from __future__ import annotations

import base64
import io as _io
import json

import numpy as np
import soundfile as sf
import torch

from comfy_api.latest import Input, io

from ..audio_cpp_client import (
    AudioCppError,
    _stage_audio,
    build_base_url,
    check_server,
    task_run,
)
from ..audio_codec import audio_dict_to_wav_bytes, resample_waveform
from ..config import DEFAULT_HOST, DEFAULT_PORT
from ..model_profiles import all_family_options


# DynamicCombo option 里排除的字段(顶层类型承接,不放进下拉)。
TASK_TOP_LEVEL_FIELDS = {"voice_ref", "target_voice", "language"}


def _input_for(param_id: str, spec):
    """把 (类型, 中文提示, 默认/选项) 转成 io 输入,类型对齐 audio.cpp API。"""
    kind, tooltip, default = spec
    kwargs = dict(id=param_id, tooltip=tooltip)
    if kind == "string":
        return io.String.Input(default=default, **kwargs)
    if kind == "path":
        # 参考音频 / 目标声音(path 类型)由顶层 AUDIO 输入承接,不下拉。
        return None
    if kind == "int":
        return io.Int.Input(default=default, **kwargs)
    if kind == "float":
        return io.Float.Input(default=default, **kwargs)
    if kind == "bool":
        return io.Boolean.Input(default=bool(default), **kwargs)
    if kind == "combo":
        options = list(default) if isinstance(default, (list, tuple)) else []
        if options:
            return io.Combo.Input(options=options, default=options[0], **kwargs)
        # 枚举列表为空(某些 spec 未列 values):退化为字符串输入。
        return io.String.Input(default="", **kwargs)
    raise ValueError(f"未知参数类型: {kind}")


def _task_options():
    """为每个音频任务模型族构建 DynamicCombo.Option(参数只列该族真实需要的)。

    参数来源 = 手工档案(TASK_PROFILES)+ 自动发现的 model_specs 新家族。
    audio.cpp 新增任务模型(如 yue2 音乐、sheetsage2 MIDI)后自动出现。
    voice_ref/language 等顶层字段不出现在下拉里。
    """
    options = []
    for family, params in all_family_options("task").items():
        inputs = []
        for name, spec in params.items():
            if name in TASK_TOP_LEVEL_FIELDS:
                continue
            inp = _input_for(name, spec)
            if inp is not None:
                inputs.append(inp)
        if not inputs:
            # 空下拉族(spec 无参数):补通用 seed,保证下拉有选项可选。
            inputs.append(io.Int.Input("seed", display_name="seed", default=-1, min=-1,
                                       tooltip="随机种子,-1 由服务器决定,不传该参数。"))
        options.append(io.DynamicCombo.Option(family, inputs))
    return options


def _decode_audio_field(data: dict, key: str):
    """把响应里的 base64 WAV 字段解码为 ComfyUI AUDIO dict,缺字段返回 None。"""
    audio_b64 = data.get(key)
    if not audio_b64:
        return None
    try:
        wav_bytes = base64.b64decode(audio_b64)
    except Exception as exc:  # noqa: BLE001 服务器字段格式错误时给出清晰错误
        raise AudioCppError(f"无法解码服务器返回的 {key}(base64 WAV 数据损坏)。") from exc
    with sf.SoundFile(_io.BytesIO(wav_bytes)) as f:
        sr = int(f.samplerate)
        samples = f.read(dtype="float32", always_2d=True)
    waveform = torch.from_numpy(np.ascontiguousarray(samples).T).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": sr}


class AudioCppAudioTask(io.ComfyNode):
    """通用音频任务:声音转换 / 音源分离 / 说话人分离 / 音乐生成 / MIDI。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppAudioTask",
            display_name="AudioCpp 音频任务",
            category="audio.cpp",
            description=(
                "调用 audio.cpp 通用任务端点(/v1/tasks/run),覆盖:\n"
                "- seed_vc            声音转换(VC)  :源音频 + 目标参考声音\n"
                "- sortformer_diar    说话人分离(DIAR):检测说话人个数与区间\n"
                "- mel_band_roformer  音源分离(SEP)  :人声/伴奏\n"
                "- minimax_music3     音乐生成(GEN)  :歌词 + 时长\n"
                "- muscriptor_small   MIDI 生成      :输出乐谱\n\n"
                "「模型」下拉是动态的:选择模型族后只显示该模型真实需要的参数。\n"
                "输入音频(AUDIO)会自动上传到服务器临时目录(需要 server 以\n"
                "--ui 或 --ui-management 启动)。\n\n"
                "输出:主音频(可选)、文本(可选)、完整结果 JSON(诊断/多路音频)。"
            ),
            search_aliases=["音频任务", "声音转换", "音源分离", "说话人分离", "音乐生成", "midi"],
            inputs=[
                io.Audio.Input(
                    "audio",
                    display_name="audio",
                    optional=True,
                    tooltip="源音频(VC/分离/说话人分离需要;音乐生成、MIDI 可留空)。",
                ),
                io.String.Input(
                    "text",
                    display_name="text",
                    multiline=True,
                    default="",
                    tooltip="文本输入:音乐生成填歌词,MIDI 生成填说明。",
                ),
                io.Audio.Input(
                    "voice_ref",
                    display_name="参考音频(目标声音)",
                    optional=True,
                    tooltip=(
                        "参考/目标声音音频(如 VC 的目标说话人)。连接 AUDIO 节点即可,\n"
                        "上传到服务器作为 voice_ref。留空则该模型参数不传。"
                    ),
                ),
                io.DynamicCombo.Input(
                    "model",
                    options=_task_options(),
                    tooltip="音频任务模型族。选择后只显示该模型需要的参数。",
                ),
                io.String.Input(
                    "host",
                    display_name="host",
                    default=DEFAULT_HOST,
                    tooltip="audio.cpp 服务器地址(默认 127.0.0.1)。",
                ),
                io.Int.Input(
                    "port",
                    display_name="port",
                    default=DEFAULT_PORT,
                    min=1,
                    max=65535,
                    tooltip="audio.cpp 服务器端口(默认 8085)。",
                ),
                io.Float.Input(
                    "timeout_seconds",
                    display_name="timeout_seconds",
                    default=600.0,
                    min=1.0,
                    max=3600.0,
                    display_mode=io.NumberDisplay.slider,
                    tooltip="请求超时时间(秒)。",
                ),
                io.Int.Input(
                    "target_sample_rate",
                    display_name="target_sample_rate",
                    default=0,
                    min=0,
                    max=192000,
                    tooltip=(
                        "把输入音频重采样到该采样率后再上传。某些模型(如 "
                        "sortformer_diar)只接受固定采样率(通常 16000),与 TTS "
                        "输出(24000)不匹配时会报 sample_rate mismatch。0 = 不重采样。"
                    ),
                ),
                io.Boolean.Input(
                    "unload_after_execute",
                    display_name="完成后卸载显存",
                    default=False,
                    tooltip=(
                        "任务完成后自动调用 /v1/tasks/unload_all_models,\n"
                        "释放 audio.cpp 模型占用的显存/内存。"
                    ),
                ),
            ],
            outputs=[
                io.Audio.Output(
                    "audio",
                    display_name="主音频",
                    tooltip="任务的主输出音频(如 VC 结果、分离的第一路)。可能为空。",
                ),
                io.String.Output(
                    "text",
                    display_name="文本",
                    tooltip="任务的文本输出(如 MIDI 乐谱说明)。",
                ),
                io.String.Output(
                    "detail",
                    display_name="完整结果(JSON)",
                    tooltip="完整响应 JSON,含 named_audio_outputs/words/artifacts/timing。",
                ),
            ],
        )

    @classmethod
    async def execute(
        cls,
        model: dict,
        host: str,
        port: int,
        timeout_seconds: float,
        audio: Input.Audio = None,
        text: str = "",
        voice_ref: Input.Audio = None,
        target_sample_rate: int = 0,
        unload_after_execute: bool = False,
    ) -> io.NodeOutput:
        if not isinstance(model, dict) or "model" not in model:
            raise AudioCppError("模型参数无效:缺少选择的模型 id。")
        family = model["model"]
        all_options = all_family_options("task")
        if family not in all_options:
            raise AudioCppError(
                f"未知音频任务模型族: {family}。该模型未在 model_specs/ 或本节点档案中定义。"
            )
        # DynamicCombo 的 dict: {'model': '<id>', '<option 参数名>': 值}
        # option 参数(顶层字段除外)进 options 对象,自动适配新家族。
        params = all_options[family]
        option_names = {k for k, v in params.items() if k not in TASK_TOP_LEVEL_FIELDS}
        # seed < 0(默认 -1,表示由服务器随机)不传:直接传负值会被服务器
        # 以 "seed must be an unsigned integer" 拒绝(HTTP 500)。
        options = {
            k: v for k, v in model.items()
            if k in option_names and v not in ("", None)
            and not (k == "seed" and isinstance(v, int) and v < 0)
        }

        # ---- 必填输入预校验(在请求服务器前给出明确错误,避免 500 晦涩报错) ----
        # 每个任务族的输入契约,来自 audio.cpp 源码(src/models/*/session.cpp):
        #   seed_vc     : 源音频(audio)+ 目标说话人参考音频(voice_ref)都必需;
        #   sortformer  : 源音频必需;
        #   minimax_music3 / muscriptor_small: 文本/歌词必需。
        needs_audio = {"seed_vc", "sortformer_diar", "mel_band_roformer"}
        needs_ref = {"seed_vc"}
        needs_text = {"minimax_music3", "muscriptor_small"}
        if family in needs_audio and audio is None:
            raise AudioCppError(
                f"{family}:该任务必须有「源音频(audio)」输入。"
                "请把要处理的音频连接到本节点的 audio 端口。"
            )
        if family in needs_ref and voice_ref is None:
            raise AudioCppError(
                f"{family}:该任务必须有「参考音频(voice_ref)」输入(目标说话人声音)。"
                "audio.cpp 的 Seed-VC 需要 target speaker reference audio;\n"
                "请把目标说话人的一段音频连接到本节点的「参考音频」端口。"
            )
        if family in needs_text and not text:
            raise AudioCppError(
                f"{family}:该任务必须有「文本(text)」输入(歌词/说明)。"
                "请在 text 输入中填写要生成的歌词或说明。"
            )

        base_url = build_base_url(host, port)
        check_server(base_url)

        wav_bytes = None
        if audio is not None:
            if target_sample_rate and int(audio.get("sample_rate", 0)) != target_sample_rate:
                audio = dict(audio)
                audio["waveform"] = resample_waveform(
                    audio["waveform"], int(audio["sample_rate"]), target_sample_rate
                )
                audio["sample_rate"] = target_sample_rate
            wav_bytes = audio_dict_to_wav_bytes(audio)

        # 参考音频(VC 目标说话人等):顶层 AUDIO 输入 → 上传 → 服务器路径
        voice_ref_path = None
        if voice_ref is not None:
            from ..interruptible import to_interruptible

            voice_ref_path = await to_interruptible(_stage_audio)(
                base_url, audio_dict_to_wav_bytes(voice_ref), "voice_ref.wav", float(timeout_seconds)
            )

        from ..interruptible import run_interruptible, to_interruptible
        from ..audio_cpp_client import unload_all_models

        try:
            result = await run_interruptible(
                to_interruptible(task_run)(
                    base_url,
                    family,
                    text=text or None,
                    audio_wav=wav_bytes,
                    voice_ref_path=voice_ref_path,
                    options=options or None,
                    timeout=float(timeout_seconds),
                ),
                base_url=base_url,
                auto_unload=unload_after_execute,
            )
        finally:
            if unload_after_execute:
                try:
                    unload_all_models(base_url, timeout=float(min(timeout_seconds, 120.0)))
                except AudioCppError:
                    pass  # 卸载失败不掩盖任务结果

        detail = json.dumps(result, ensure_ascii=False)
        main_audio = _decode_audio_field(result, "audio")
        out_text = result.get("text", "") or ""
        return io.NodeOutput(main_audio, out_text, detail)