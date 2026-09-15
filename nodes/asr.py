"""AudioCppSpeechToText: 语音转文本(ASR)节点。

调用本地 audio.cpp 服务器的 /v1/audio/transcriptions 端点。
上传前把 AUDIO 波形在本地编码为 WAV(multipart/form-data,file 字段),
这是服务器端唯一保证支持的输入格式(mp3/flac 解码是可选的)。

输出类型:
- text      : 转写文本(STRING)
- language  : 检测/配置的语言代码(STRING)
- detail    : 完整响应 JSON(STRING),含 words(timing)等诊断信息
错误处理:连接失败 / 服务器返回 4xx/5xx 会抛 AudioCppError,并附带服务器原始错误消息。
"""

from __future__ import annotations

from comfy_api.latest import Input, io

from ..audio_cpp_client import AudioCppError, build_base_url, check_server, transcribe
from ..audio_codec import audio_dict_to_wav_bytes
from ..config import DEFAULT_HOST, DEFAULT_PORT


class AudioCppSpeechToText(io.ComfyNode):
    """语音转文本:把音频交给 audio.cpp 服务器的 ASR 模型,返回转写文字。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppSpeechToText",
            display_name="AudioCpp 语音转文本",
            category="audio.cpp",
            description=(
                "把 AUDIO 输入发送给 audio.cpp 服务器的 ASR 模型(如 qwen3_asr),\n"
                "返回转写文本。上传前会把波形在本地编码为 WAV(multipart file 字段),\n"
                "这是服务器端唯一保证支持的输入格式。\n\n"
                "需要本地已启动 audiocpp_server(默认 http://127.0.0.1:8085)。\n"
                "若服务器未启动或请求失败,节点会报错并给出服务器原始错误消息。"
            ),
            search_aliases=["asr", "stt", "转写", "语音识别", "audio.cpp asr"],
            inputs=[
                io.Audio.Input(
                    "audio",
                    display_name="audio",
                    tooltip="要转写的音频(waveform [B,C,T])。",
                ),
                io.Combo.Input(
                    "model",
                    display_name="model",
                    options=["qwen3_asr"],
                    default="qwen3_asr",
                    tooltip="ASR 模型 id。",
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
                io.Combo.Input(
                    "language",
                    display_name="language",
                    options=["auto", "zh", "en", "yue", "ja", "ko", "de", "fr", "es", "zh dialects"],
                    default="auto",
                    tooltip=(
                        "音频语言代码(自动检测 / 指定语言)。留空或 auto 表示自动检测。\n"
                        "qwen3_asr 支持 30+ 种语言,常见列表见上。"
                    ),
                ),
                io.String.Input(
                    "prompt",
                    display_name="prompt",
                    default="",
                    tooltip="可选的引导提示文本(热词、专有名词、上下文等)。",
                ),
                io.Float.Input(
                    "timeout_seconds",
                    display_name="timeout_seconds",
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
        model: str,
        host: str,
        port: int,
        timeout_seconds: float,
        language: str = "",
        prompt: str = "",
        unload_after_execute: bool = False,
    ) -> io.NodeOutput:
        # audio 是必填输入;缺失或为空时先给出明确错误,避免 numpy 底层晦涩异常。
        if audio is None:
            raise AudioCppError("语音转文本需要连接「audio」输入(要转写的音频)。")
        wav_bytes = audio_dict_to_wav_bytes(audio)
        base_url = build_base_url(host, port)
        check_server(base_url)
        from ..interruptible import run_interruptible, to_interruptible
        from ..audio_cpp_client import unload_all_models

        try:
            result = await run_interruptible(
                to_interruptible(transcribe)(
                    base_url,
                    model,
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
        import json

        detail = json.dumps(result, ensure_ascii=False)
        return io.NodeOutput(text, lang or "", detail)