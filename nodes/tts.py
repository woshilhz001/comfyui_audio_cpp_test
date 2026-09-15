"""AudioCppTextToSpeech: 文本转语音节点。

调用本地 audio.cpp 服务器的 /v1/audio/speech 端点合成语音。

设计理念(与 ComfyUI 生态对齐):
- 「model」下拉用 DynamicCombo:选模型族只显示该族真实需要的参数。
- 参考音频(voice_ref)是顶层可选 AUDIO 输入 —— 从 ComfyUI 上游节点接音频,
  上传到服务器临时目录/回流路径后传给 API,而不是手工填服务器文件路径。
- 语言(language)是下拉:数据源来自 model_specs/*.json 的 languages 字段,
  自动发现新模型的可用语言。
- 参数类型严格对齐 audio.cpp API(string/int/float/bool/enum)。
"""

from __future__ import annotations

from comfy_api.latest import Input, io

from ..audio_cpp_client import (
    AudioCppError,
    _stage_audio,
    build_base_url,
    check_server,
    speech_synthesize,
)
from ..audio_codec import audio_dict_to_wav_bytes, wav_bytes_to_audio_dict
from ..config import DEFAULT_HOST, DEFAULT_PORT
from ..model_profiles import all_family_options, spec_languages


# DynamicCombo option 里排除,放到节点顶层的字段(类型不放进下拉)。
# reference_text 必须在内:否则它会同时出现在顶层面和 DynamicCombo 下拉里,
# 造成 qwen3_tts_base 等家族出现两个 reference_text 输入的界面 bug。
TOP_LEVEL_FIELDS = {"voice_ref", "reference_text", "language", "seed", "instructions"}


def _input_for(param_id: str, spec):
    """把 (类型, 中文提示, 默认/选项) 转成 io 输入。类型严格对齐 audio.cpp API。"""
    kind, tooltip, default = spec
    kwargs = dict(id=param_id, tooltip=tooltip)
    if kind == "string":
        return io.String.Input(default=default, **kwargs)
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
        return io.String.Input(default="", **kwargs)
    if kind == "path":
        # path 类型(参考音频等)不在下拉里;由顶层 AUDIO 输入承接。
        return None
    raise ValueError(f"未知参数类型: {kind}")


def _tts_options():
    """为每个 TTS 模型族构建 DynamicCombo.Option(排除顶层字段,避免重复)。

    参数来源 = 手工档案(TTS_PROFILES)+ 自动发现的 model_specs 新家族。
    空下拉族(spec 无参数、或参数全被提升到顶层)补一个通用 seed 输入,
    保证下拉总有一个可选项 —— 否则 ComfyUI 的 DynamicCombo 无法选中该族。
    """
    options = []
    for family, params in all_family_options("tts").items():
        inputs = []
        for name, spec in params.items():
            if name in TOP_LEVEL_FIELDS:
                continue
            inp = _input_for(name, spec)
            if inp is not None:
                inputs.append(inp)
        if not inputs:
            # 空下拉族:补通用 seed,保证下拉有选项可选。
            inputs.append(io.Int.Input("seed", display_name="seed", default=-1, min=-1,
                                       tooltip="随机种子,-1 由服务器决定,不传该参数。"))
        options.append(io.DynamicCombo.Option(family, inputs))
    return options


def _language_options() -> list[str]:
    """语言下拉选项。

    - 数据源:各家族 model_specs languages 的并集;
    - 额外固定写入常见语言代码与旧 workflow 兼容别名(中文显示名等),
      保证已有工作流里的 language 值不会因下拉校验失败而整图被拒。
    """
    # 固定选项:常见语言代码 + 旧工作流的兼容值(历史 STRING 输入留下的)
    fixed = [
        "auto", "english", "zh", "en", "ja", "ko", "de", "fr", "ru", "es",
        "it", "pt", "yue",
        # ----- 旧 workflow 兼容别名(保持在校验集合内) -----
        "中文 (Chinese)", "中文", "英语 (English)", "英语",
        "custom", "chinese", "mandarin",
    ]
    langs = set(fixed)
    for family in all_family_options("tts"):
        langs.update(spec_languages(family))
    # 保持 fixed 的顺序,spec 补充的语言追加在后
    return fixed + sorted(langs - set(fixed))


class AudioCppTextToSpeech(io.ComfyNode):
    """文本转语音:向 audio.cpp 服务器发送文本,返回合成语音音频。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppTextToSpeech",
            display_name="AudioCpp 文本转语音",
            category="audio.cpp",
            description=(
                "把文本发送给 audio.cpp 服务器的 TTS 模型,合成语音并输出 AUDIO。\n"
                "需要本地已启动 audiocpp_server(默认 http://127.0.0.1:8085)。\n\n"
                "「模型」下拉:选择不同 TTS 模型族后,只显示该模型真正需要的参数。\n"
                "「参考音频 voice_ref」是可选 AUDIO 输入(克隆/参考声音),连接上游\n"
                "音频节点即可,自动上传到服务器;留空则用模型内置音色 voice。\n"
                "「语言 language」为下拉,数据源来自 audio.cpp 的 model_specs。\n\n"
                "Qwen3 系列参数差异:\n"
                "- qwen3_tts(VoiceDesign):填「音色设计描述 instructions」;\n"
                "- qwen3_tts_base(克隆) :接 voice_ref + 填 reference_text;\n"
                "- qwen3_tts_customvoice:填 voice(如 Vivian)。"
            ),
            search_aliases=["tts", "文本转语音", "语音合成", "audio.cpp tts"],
            inputs=[
                io.String.Input(
                    "text",
                    display_name="text",
                    multiline=True,
                    default="",
                    tooltip="要合成的文本内容。",
                ),
                io.DynamicCombo.Input(
                    "model",
                    options=_tts_options(),
                    tooltip="TTS 模型族。选择后只显示该模型需要的参数。",
                ),
                # 顶层可选输入:参考音频/克隆声音(上传到服务器)
                io.Audio.Input(
                    "voice_ref",
                    display_name="参考音频(克隆)",
                    optional=True,
                    tooltip=(
                        "克隆/参考声音音频。连接上游 AUDIO 节点即可,上传到服务器;\n"
                        "配合 reference_text 使用(如 qwen3_tts_base)。留空则用 voice 内置音色。"
                    ),
                ),
                io.String.Input(
                    "reference_text",
                    display_name="reference_text",
                    default="",
                    tooltip="参考音频(voice_ref)对应的文本转写,克隆模型要求。",
                ),
                io.String.Input(
                    "instructions",
                    display_name="instructions",
                    default="",
                    tooltip="音色设计描述(VoiceDesign 模型 qwen3_tts 需要),如「温暖的中年播音员」。",
                ),
                io.String.Input(
                    "voice",
                    display_name="voice",
                    default="",
                    tooltip="内置音色 id(如 qwen3_tts_customvoice 的 Vivian、Ryan)。",
                ),
                io.Combo.Input(
                    "language",
                    display_name="language",
                    options=_language_options(),
                    default="english",
                    tooltip="语言代码(来自 audio.cpp 各家族的 languages)。auto 表示自动。",
                ),
                io.Int.Input(
                    "seed",
                    display_name="seed",
                    default=-1,
                    min=-1,
                    control_after_generate=io.ControlAfterGenerate.randomize,
                    tooltip="随机种子,-1 由服务器决定,不传该参数。",
                ),
                io.Boolean.Input(
                    "unload_after_execute",
                    display_name="完成后卸载显存",
                    default=False,
                    tooltip=(
                        "生成完成后自动调用 audio.cpp 的全量卸载接口\n"
                        "(/v1/tasks/unload_all_models),释放该模型占用的显存/内存。\n"
                        "适合生成完就要让出显存的场景;留 False 则模型保留在内存复用。"
                    ),
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
                    tooltip="请求超时时间(秒)。长句或慢速模型可能需要调大。",
                ),
            ],
            outputs=[
                io.Audio.Output(
                    "audio",
                    display_name="语音",
                    tooltip="合成出的语音,waveform [B,C,T] float32 + sample_rate。",
                ),
            ],
        )

    @classmethod
    async def execute(
        cls,
        text: str,
        model: dict,
        host: str,
        port: int,
        timeout_seconds: float,
        voice_ref: Input.Audio = None,
        reference_text: str = "",
        instructions: str = "",
        voice: str = "",
        language: str = "english",
        seed: int = -1,
        unload_after_execute: bool = False,
    ) -> io.NodeOutput:
        if not text:
            raise AudioCppError("文本转语音需要非空的「text」输入。")
        if not isinstance(model, dict) or "model" not in model:
            raise AudioCppError("模型参数无效:缺少选择的模型 id。")

        family = model["model"]
        all_options = all_family_options("tts")
        if family not in all_options:
            raise AudioCppError(
                f"未知 TTS 模型族: {family}。该模型未在 model_specs/ 或本节点档案中定义。"
            )
        params = all_options[family]

        # DynamicCombo 的 dict 形态: {'model': '<id>', '<option 参数名>': 值}
        # option 参数(排除顶层字段)放进 options 对象,自动适配新家族。
        p = {k: v for k, v in model.items() if k in params and k not in TOP_LEVEL_FIELDS}
        extra_options = {k: v for k, v in p.items() if v not in ("", None)}

        # ---- 必填输入预校验(避免服务器晦涩 500) ----
        # qwen3_tts_base 是克隆模型,必须提供参考音频(voice_ref)才能合成;
        # qwen3_tts(VoiceDesign)必须提供 instructions 音色描述。
        if family == "qwen3_tts_base" and voice_ref is None:
            raise AudioCppError(
                "qwen3_tts_base 是克隆模型:必须连接「参考音频(voice_ref)」"
                "并填写 reference_text,才能克隆声音。\n"
                "提示:先合成一段参考语音(AudioCpp 文本转语音),"
                "或接入一段已有的语音音频。"
            )
        if family == "qwen3_tts" and not instructions:
            raise AudioCppError(
                "qwen3_tts 是 VoiceDesign 模型:必须填写「音色设计描述(instructions)」"
                "才能设计声音,例如「一位温暖的中年女性解说员」。"
            )

        base_url = build_base_url(host, port)
        check_server(base_url)  # 区分「服务器未启动」与「请求失败」

        # 语言归一化:兼容旧 workflow 的中文显示名,映射回 audio.cpp 语言代码。
        # 注意 qwen3_tts 系列接受完整语言词(english/chinese),不接受 en/zh。
        # 未列出的值原样传服务器;错误由服务器明确报告。
        language_code = {
            "中文 (Chinese)": "chinese",
            "中文": "chinese",
            "chinese": "chinese",
            "mandarin": "chinese",
            "zh": "chinese",
            "英语 (English)": "english",
            "英语": "english",
            "custom": "english",
            "en": "english",
            "auto": "",
        }.get(language, language)
        if language_code in ("", None):
            language_code = None

        # 参考音频:经 /v1/ui/upload 上传,得到服务器端路径传给 API
        voice_ref_path = None
        if voice_ref is not None:
            from ..interruptible import to_interruptible

            voice_ref_path = await to_interruptible(_stage_audio)(
                base_url, audio_dict_to_wav_bytes(voice_ref), "voice_ref.wav", float(timeout_seconds)
            )

        # 通过可中断包装调用:async execute + 响应 ComfyUI 停止按钮。
        # 生成完成(或中断)后按 unload_after_execute 决定是否释放显存。
        from ..interruptible import run_interruptible, to_interruptible

        wav_bytes = await run_interruptible(
            to_interruptible(speech_synthesize)(
                base_url,
                family,
                text,
                voice=voice if isinstance(voice, str) and voice else None,
                voice_ref_path=voice_ref_path,
                reference_text=reference_text if isinstance(reference_text, str) and reference_text else None,
                instructions=instructions or None,
                language=language_code,
                seed=seed if isinstance(seed, int) and seed >= 0 else None,
                extra_options=extra_options or None,
                timeout=float(timeout_seconds),
            ),
            base_url=base_url,
            auto_unload=unload_after_execute,
        )
        from ..audio_cpp_client import unload_all_models

        if unload_after_execute:
            try:
                unload_all_models(base_url, timeout=float(min(timeout_seconds, 120.0)))
            except AudioCppError:
                pass  # 卸载失败不掩盖生成结果
        return io.NodeOutput(wav_bytes_to_audio_dict(wav_bytes))