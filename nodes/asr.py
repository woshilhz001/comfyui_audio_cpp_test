"""AudioCppSpeechToText: 语音转文本(ASR)节点。

调用本地 audio.cpp 服务器的 /v1/audio/transcriptions 端点。
上传前把 AUDIO 波形在本地编码为 WAV(multipart/form-data,file 字段),
这是服务器端唯一保证支持的输入格式(mp3/flac 解码是可选的)。

「model」用 DynamicCombo:选择模型族后只显示该族支持的语言下拉
(来自 model_specs 的 languages 字段,过滤 "50+ languages" 等占位条目);
prompt 对所有 ASR 家族都适用,保留在节点顶层。

输出类型:
- text      : 转写文本(STRING)
- language  : 检测/配置的语言代码(STRING)
- detail    : 完整响应 JSON(STRING),含 words(timing)等诊断信息
错误处理:连接失败 / 服务器返回 4xx/5xx 会抛 AudioCppError,并附带服务器原始错误消息。
"""

from __future__ import annotations

import json

from comfy_api.latest import Input, io

from ..audio_cpp_client import AudioCppError, build_base_url, check_server, transcribe
from ..audio_codec import audio_dict_to_wav_bytes
from ..config import DEFAULT_HOST, DEFAULT_PORT
from ..model_profiles import all_family_options, family_language_options


def _asr_options() -> list[io.DynamicCombo.Option]:
    """ASR 模型 DynamicCombo 选项:每个家族 Option 只含该族支持的语言下拉。

    注意:transcriptions 端点的 multipart 表单只接受
    file / model / language / prompt 字段,家族专属参数无法经 multipart
    传入;因此 Option 里只有 language 一项(prompt 在节点顶层)。
    语言来自 model_specs 的 languages 字段(占位条目已过滤),
    首项 "auto" 表示自动检测;spec 未声明语言时退回常见语言代码。
    """
    options = []
    for family in all_family_options("asr"):
        options.append(io.DynamicCombo.Option(
            family,
            [io.Combo.Input(
                "language",
                display_name="语言",
                options=family_language_options(family, first="auto"),
                default="auto",
                tooltip=(
                    "该模型支持的语言(来自 model spec)。"
                    "auto = 服务器自动检测。"
                ),
            )],
        ))
    return options


class AudioCppSpeechToText(io.ComfyNode):
    """语音转文本:把音频交给 audio.cpp 服务器的 ASR 模型,返回转写文字。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppSpeechToText",
            display_name="AudioCpp 语音转文本",
            category="audio.cpp",
            description=(
                "把 AUDIO 输入发送给 audio.cpp 服务器的 ASR 模型\n"
                "(如 qwen3_asr、sense_asr、moonshine_asr 等),\n"
                "返回转写文本。上传前会把波形在本地编码为 WAV(multipart file 字段),\n"
                "这是服务器端唯一保证支持的输入格式。\n\n"
                "「模型」下拉是动态的:选择模型族后只显示该模型支持的语言。\n"
                "需要本地已启动 audiocpp_server(默认 http://127.0.0.1:8085)。\n"
                "若服务器未启动或请求失败,节点会报错并给出服务器原始错误消息。"
            ),
            search_aliases=["asr", "stt", "转写", "语音识别", "audio.cpp asr"],
            inputs=[
                io.Audio.Input(
                    "audio",
                    display_name="音频",
                    tooltip="要转写的音频(waveform [B,C,T])。",
                ),
                io.DynamicCombo.Input(
                    "model",
                    display_name="模型",
                    options=_asr_options(),
                    tooltip=(
                        "ASR 模型 id(来自 server.json 配置,自动发现 spec 家族)。\n"
                        "选择后只显示该模型支持的语言。"
                    ),
                ),
                io.String.Input(
                    "host",
                    display_name="服务器地址",
                    default=DEFAULT_HOST,
                    tooltip="audio.cpp 服务器地址(默认 127.0.0.1)。",
                ),
                io.Int.Input(
                    "port",
                    display_name="端口",
                    default=DEFAULT_PORT,
                    min=1,
                    max=65535,
                    tooltip="audio.cpp 服务器端口(默认 8085)。",
                ),
                io.String.Input(
                    "prompt",
                    display_name="引导提示",
                    default="",
                    tooltip="可选的引导提示文本(热词、专有名词、上下文等)。",
                ),
                io.Float.Input(
                    "timeout_seconds",
                    display_name="超时时间(秒)",
                    default=600.0,
                    min=1.0,
                    max=3600.0,
                    display_mode=io.NumberDisplay.slider,
                    tooltip="请求超时时间(秒)。音频较长或模型较慢时需要调大。",
                ),
                io.Boolean.Input(
                    "unload_after_execute",
                    display_name="完成后卸载显存",
                    default=False,
                    tooltip=(
                        "转写完成后自动调用 /v1/tasks/unload_all_models,\n"
                        "释放 audio.cpp 模型占用的显存/内存。"
                    ),
                ),
                io.Boolean.Input(
                    "auto_register",
                    display_name="自动注册未注册模型",
                    default=True,
                    tooltip=(
                        "执行前检查服务器注册表,若该模型尚未注册则自动调\n"
                        "POST /v1/models/load 补注册(需服务器开 --ui-management)。\n"
                        "已注册的模型不受影响(幂等)。"
                    ),
                ),
            ],
            outputs=[
                io.String.Output(
                    "text",
                    display_name="转写文本",
                    tooltip="ASR 模型转写出的文本。",
                ),
                io.String.Output(
                    "language",
                    display_name="语言",
                    tooltip="检测或配置的语言代码。",
                ),
                io.String.Output(
                    "detail",
                    display_name="详细结果(JSON)",
                    tooltip="完整响应 JSON,含 timing/words 等诊断信息。",
                ),
            ],
        )

    @classmethod
    async def execute(
        cls,
        audio: Input.Audio,
        model: dict,
        host: str,
        port: int,
        timeout_seconds: float,
        prompt: str = "",
        unload_after_execute: bool = False,
        auto_register: bool = True,
    ) -> io.NodeOutput:
        # audio 是必填输入;缺失或为空时先给出明确错误,避免 numpy 底层晦涩异常。
        if audio is None:
            raise AudioCppError("语音转文本需要连接「audio」输入(要转写的音频)。")
        if not isinstance(model, dict) or "model" not in model:
            raise AudioCppError("模型参数无效:缺少选择的模型 id。")
        family = model["model"]
        if family not in all_family_options("asr"):
            raise AudioCppError(
                f"未知 ASR 模型族: {family}。该模型未在 model_specs/ 或本节点档案中定义。"
            )
        # 语言从 DynamicCombo 的 family Option dict 读取(每族只显示自己支持的语言)
        language = str(model.get("language", "") or "")
        wav_bytes = audio_dict_to_wav_bytes(audio)
        base_url = build_base_url(host, port)
        check_server(base_url)

        from ..interruptible import run_interruptible, to_interruptible
        from ..audio_cpp_client import ensure_model_registered, unload_all_models

        # 自动注册:新下载/放入 models/ 的模型未注册时首次请求会 500
        # "unknown model id"。运行时补注册(幂等),失败给中文指引。
        if auto_register:
            await run_interruptible(
                to_interruptible(ensure_model_registered)(
                    base_url, family, timeout=float(timeout_seconds)
                ),
                base_url=base_url,
            )

        try:
            result = await run_interruptible(
                to_interruptible(transcribe)(
                    base_url,
                    family,
                    wav_bytes,
                    language=(language if language and language != "auto" else None),
                    prompt=prompt.strip() or None,
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
                    pass  # 卸载失败不掩盖转写结果
        if not isinstance(result, dict):
            raise AudioCppError(f"ASR 响应格式异常:{result!r}")
        text = result.get("text", "")
        lang = result.get("language", "")
        detail = json.dumps(result, ensure_ascii=False)
        return io.NodeOutput(text, lang or "", detail)
