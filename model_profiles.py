"""模型档案与自动适配层。

两部分数据合并成节点展示的参数:
1. 运行时自动发现: 读 audio.cpp 的 model_specs/*.json(options.request),
   任何新模型(audio.cpp 升级后)只要带 spec 就会出现,无需改本节点代码。
2. 手工补充档案: 本文件的 TTS_PROFILES / TASK_PROFILES / ASR_PROFILES,
   用于提供中文提示、合理默认值,并补充 spec 缺少参数的旧格式模型
   (qwen3_tts 等内嵌 GGUF 的家族)。

字段语义:
    type "string" / "enum" -> io.String / io.Combo
    type "int"             -> io.Int
    type "float"           -> io.Float
    type "bool"            -> io.Boolean
    type "path"            -> io.String(服务器端文件路径)

新增模型接入流程(给后期维护者):
    a) audio.cpp 升级后 model_specs/*.json 自带新家族 -> 节点自动显示,
       前提是模型已写进 server.json(服务器 /v1/models 会列出)。
    b) 若新家族有特殊参数或需要中文提示,在下面的档案 dict 里追加
       一项,用 family 名作 key 即可覆盖/补充 spec 的参数。
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import MODEL_SPEC_DIRS

# ---- 手工补充档案(优先于自动发现的 spec) ----

# TTS 家族: family -> (task, {参数名: (类型, 中文提示, 默认值或选项)})
TTS_PROFILES = {
    "qwen3_tts": (
        "tts",
        {
            "voice": ("string", "内置音色 id(VoiceDesign 需 instructions,本族内置音色用 VoiceDesign 描述)", ""),
            "instructions": ("string", "音色设计描述,例如「温暖的中年播音员」", ""),
            "language": ("string", "语言代码(auto 自动检测)", "auto"),
        },
    ),
    "qwen3_tts_base": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径(必需)", ""),
            "reference_text": ("string", "参考音频对应的文本转写(必需)", ""),
            "language": ("string", "语言代码", "english"),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
        },
    ),
    "qwen3_tts_customvoice": (
        "tts",
        {
            "voice": ("string", "内置音色 id,如 Vivian、Ryan", "Vivian"),
            "language": ("string", "语言代码", "english"),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
        },
    ),
    "cosyvoice3": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "instruct": ("string", "指令文本(instruct 模式;与 reference_text 搭配使用)", ""),
            "template_name": ("combo", "CosyVoice3 请求模板", ["zero_shot", "cross_lingual", "instruct"]),
            "text_chunk_size": ("int", "长文本分块大小", 600),
            "text_chunk_mode": ("combo", "分块模式", ["default", "tag_aware", "japanese", "endline"]),
            "max_tokens": ("int", "最大生成 token 数(-1 不限)", -1),
            "min_tokens": ("int", "最小生成 token 数(-1 不限)", -1),
            "top_k": ("int", "Top-K 采样", 25),
            "num_inference_steps": ("int", "Flow 解码步数", 10),
            "seed": ("int", "随机种子", 1986),
        },
    ),
    "omnivoice": (
        "tts",
        {
            "voice": ("string", "内置音色 id(OmniVoice 支持的内置语音)", ""),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
        },
    ),
    "chatterbox_turbo": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "max_tokens": ("int", "最大生成 token 数", 1000),
            "text_chunk_size": ("int", "长文本分块大小", 128),
            "text_chunk_mode": ("combo", "分块模式", ["default", "tag_aware", "japanese", "endline"]),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
        },
    ),
    # ---- audio.cpp 新增 TTS 家族(中文档案,参数描述中文化) ----
    "auk": (
        "tts",
        {
            "instruct": ("string", "指令文本(编辑/风格控制)", ""),
            "duration_sec": ("float", "生成时长(秒),留空自动", 0.0),
            "num_inference_steps": ("int", "推理步数", 32),
            "guidance_scale": ("float", "无分类器引导强度", 2.0),
            "sway_sampling_coef": ("float", "Sway 采样系数(-1 用默认)", -1.0),
            "seed": ("int", "随机种子,-1 由服务器决定", -1),
        },
    ),
    "kitten_tts": (
        "tts",
        {
            "speed": ("float", "语速倍率", 1.0),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
            "text_chunk_mode": ("combo", "长文本分块模式", ["default", "tag_aware", "japanese", "endline"]),
            "text_chunk_size": ("int", "长文本分块大小", 400),
        },
    ),
    "moss_tts_v15": (
        "tts",
        {
            "instruct": ("string", "指令文本(情感/口音等控制)", ""),
            "language": ("string", "语言代码", ""),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
            "temperature": ("float", "采样温度", 1.5),
            "top_k": ("int", "Top-K 采样", 50),
            "top_p": ("float", "核采样概率", 0.6),
            "repetition_penalty": ("float", "重复惩罚", 1.1),
            "text_chunk_size": ("int", "长文本分块大小", 200),
        },
    ),
    "moss_ttsd": (
        "tts",
        {
            "voice_ref": ("path", "克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "language": ("string", "语言代码", ""),
            "instruct": ("string", "指令文本", ""),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
            "temperature": ("float", "采样温度", 1.5),
            "top_p": ("float", "核采样概率", 0.6),
            "top_k": ("int", "Top-K 采样", 50),
            "repetition_penalty": ("float", "重复惩罚", 1.1),
        },
    ),
    "piper_tts": (
        "tts",
        {
            "speed": ("float", "语速倍率", 1.0),
            "variation": ("float", "发声变异度(口音随机性)", 0.667),
            "duration_variation": ("float", "时长变异度", 0.8),
            "seed": ("int", "随机种子", 1234),
            "text_chunk_size": ("int", "长文本分块大小", 280),
        },
    ),
    "zipvoice": (
        "tts",
        {
            "voice_ref": ("path", "克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "guidance_scale": ("float", "无分类器引导强度", 3.0),
            "num_inference_steps": ("int", "推理步数", 8),
            "t_shift": ("float", "时间偏移参数", 0.5),
            "speed": ("float", "语速倍率", 1.0),
            "feat_scale": ("float", "特征缩放", 0.1),
            "target_rms": ("float", "目标 RMS 电平", 0.1),
            "seed": ("int", "随机种子", 666),
            "lang": ("string", "语言代码(如 en-us)", "en-us"),
        },
    ),
}

# TTS 家族可以由规范中的任务类别自动识别: 若新家族 spec 有 "tts" 任务
# 且未在 TTS_PROFILES 中硬编码,则由 ModelSpecLoader 自动挂到 TTS 节点。

ASR_PROFILES = {
    "qwen3_asr": (
        "asr",
        {
            "language": ("string", "语言代码,留空自动检测", ""),
            "prompt": ("string", "引导转写的提示文本(热词等)", ""),
        },
    ),
    # ---- audio.cpp 新增 ASR 家族(中文档案) ----
    "canary_asr": (
        "asr",
        {
            "language": ("string", "语言代码(默认 en)", "en"),
            "target_language": ("string", "目标语言(翻译场景,留空不译)", ""),
            "pnc": ("bool", "启用标点与大小写恢复", True),
            "max_tokens": ("int", "最大 token 数(0 用默认)", 0),
            "audio_chunk_mode": ("combo", "长音频分块模式", ["auto", "fixed", "none"]),
            "audio_chunk_duration_sec": ("float", "分块时长(秒)", 40.0),
        },
    ),
    "cohere_asr": (
        "asr",
        {
            "language": ("string", "语言代码(默认 en)", "en"),
            "pnc": ("bool", "启用标点与大小写恢复", True),
            "max_tokens": ("int", "最大 token 数", 256),
            "audio_chunk_mode": ("combo", "长音频分块模式", ["auto", "quiet_energy", "fixed", "none"]),
            "audio_chunk_duration_sec": ("float", "分块时长(秒)", 35.0),
        },
    ),
    "confucius4_r2t2": (
        "asr",
        {
            "language": ("string", "语言代码(默认 Auto 检测)", "Auto"),
            "max_tokens": ("int", "最大 token 数(0 用默认)", 0),
        },
    ),
    "moss_transcribe_diarize": (
        "asr",
        {
            "max_tokens": ("int", "最大 token 数(0 用默认)", 0),
            "instruct": ("string", "指令文本(可选)", ""),
        },
    ),
}

# 音频任务家族: family -> (task, 任务中文名, {参数名: spec})
# 这些家族都通过通用任务端点 POST /v1/tasks/run 调用(见 nodes/task.py)。
# model_profiles 的 key 是「服务器配置的模型 id」(server.json 里的 id);
# 自动发现的 spec 家族(同名 id)会与这里合并/覆盖。
TASK_PROFILES = {
    # ============ 音乐生成 ============
    "minimax_music3": (
        "gen",
        "音乐生成",
        {
            "lyrics": ("string", "歌词,[verse] 等结构标签", ""),
            "duration_sec": ("float", "生成音频时长(秒)", 20.0),
            "num_inference_steps": ("int", "Flow 匹配步数/块", 30),
            "guidance_scale": ("float", "Flow 无分类器引导强度", 1.7),
            "ar_guidance_scale": ("float", "自回归语义引导强度", 1.5),
            "top_k": ("int", "Top-K 采样", 50),
            "seed": ("int", "随机种子", 0),
        },
    ),
    "yue2": (
        "gen",
        "音乐生成(YuE2:ABC 规划 + 语义码本 + NAR 声学流)",
        {
            "style": ("string", "歌曲风格/标签(如 rock、ballad,jpop)", ""),
            "lyrics": ("string", "歌词文本(必填)", ""),
            "cot": ("combo", "符号规划路线:off 跳过 ABC;melody/full 先生成 ABC 再进音乐 token", ["off", "melody", "full"]),
            "abc": ("string", "外部 ABC 乐谱文本(melody/full 路线可用)", ""),
            "guidance_scale": ("float", "语义无分类器引导强度(0~20)", 0.0),
            "seed": ("int", "随机种子", 1234),
            "num_inference_steps": ("int", "NAR 中点 ODE 步数", 8),
            "abc_temperature": ("float", "ABC 规划器采样温度(0 用默认)", 0.0),
            "abc_top_p": ("float", "ABC 规划器核采样概率(0 用默认)", 0.0),
            "abc_top_k": ("int", "ABC 规划器 top-k 限制(0 用默认)", 0),
            "abc_repetition_penalty": ("float", "ABC 规划器重复惩罚(0 用默认)", 0.0),
            "semantic_temperature": ("float", "语义码本采样温度(0 用默认)", 0.0),
            "semantic_top_p": ("float", "语义码本核采样概率(0 用默认)", 0.0),
            "semantic_top_k": ("int", "语义码本 top-k 限制(0 用默认)", 0),
            "semantic_repetition_penalty": ("float", "语义码本重复惩罚(0 用默认)", 0.0),
        },
    ),
    "heartmula": (
        "gen",
        "音乐生成(HeartMuLa 歌词驱动)",
        {
            "lyrics": ("string", "歌词文本(必填)", ""),
            "tags": ("string", "逗号分隔的音乐标签(风格)", ""),
            "duration_sec": ("float", "最大生成时长(秒,须为正)", 120.0),
            "temperature": ("float", "音频 token 采样温度", 1.0),
            "top_k": ("int", "音频 token top-k 限制", 50),
            "guidance_scale": ("float", "MuLa 无分类器引导强度", 1.5),
            "num_inference_steps": ("int", "Codec 流求解步数", 10),
            "infinite_mode": ("bool", "拆分歌词生成长音频", False),
            "seed": ("int", "随机种子", 1234),
        },
    ),
    "midashenglm_gen": (
        "gen",
        "音乐生成(MiDaShengLM)",
        {
            "duration_sec": ("float", "目标音频时长(秒)", 20.0),
            "guidance_scale": ("float", "无分类器引导强度", 2.0),
            "stop_threshold": ("float", "停止概率阈值", 0.5),
            "min_stop_step": ("int", "接受停止前的最小生成步数", 5),
            "seed": ("int", "随机种子,-1 表示随机", 0),
        },
    ),
    # ============ 乐谱转写(MIDI / ABC) ============
    "muscriptor_small": (
        "midi",
        "MIDI 生成(生成乐谱)",
        {
            "instruments": ("string", "逗号分隔的乐器组名", ""),
            "output_format": ("combo", "输出序列化格式", ["midi", "json"]),
            "max_tokens": ("int", "最大生成 token 数", 2000),
            "do_sample": ("bool", "使用采样而非贪心", False),
            "temperature": ("float", "采样温度", 1.0),
            "guidance_scale": ("float", "无分类器引导系数", 1.0),
            "batch_size": ("int", "每批音频块数", 1),
            "num_beams": ("int", "束搜索宽度", 1),
            "prelude_forcing": ("bool", "强制打开前奏 token", True),
            "seed": ("int", "随机种子", 0),
        },
    ),
    "sheetsage2": (
        "midi",
        "乐谱转写(SheetSage2:音频 → ABC)",
        {
            "max_tokens": ("int", "最大解码序列长度(0 跟随模型上下文)", 0),
        },
    ),
    # ============ 声音转换 / 音源分离 / 增强 ============
    "seed_vc": (
        "vc",
        "声音转换",
        {
            "voice_ref": ("path", "目标声音参考音频 WAV 路径(目标说话人)", ""),
            "route": ("combo", "Seed-VC 转换路线", ["v2_vc", "v1_svc", "v1_whisper_bigvgan_vc", "v1_xlsr_hift_vc"]),
            "length_adjust": ("float", "输出时长倍率(必须为正)", 1.0),
            "num_inference_steps": ("int", "扩散步数", 30),
            "inference_guidance_scale": ("float", "V1 无分类器引导强度", 0.7),
            "intelligibility_guidance_scale": ("float", "V2 清晰度引导强度", 0.7),
            "similarity_guidance_scale": ("float", "V2 相似度引导强度", 0.7),
            "voice_anonymization": ("bool", "使用随机平均声线条件(匿名化)", False),
            "f0_condition": ("bool", "开启 V1 基频条件(唱歌转换)", False),
            "auto_f0_adjust": ("bool", "自动调整源音高向目标", False),
            "semitone_shift": ("int", "V1 音高偏移(半音)", 0),
            "seed": ("int", "随机种子,0 由服务器决定", 0),
        },
    ),
    "rvc": (
        "vc",
        "声音转换(RVC)",
        {
            "voice_id": ("combo", "打包 RVC 音色 id", ["default", "manthos", "chocola", "fraise"]),
            "pitch_extractor": ("combo", "F0 音高提取方法", ["rmvpe"]),
            "semitone_shift": ("int", "转换前的半音移调", 0),
            "pitch_filter_radius": ("int", "F0 平滑中值滤波半径", 3),
            "output_sample_rate": ("int", "输出采样率(0 保持音色原采样率)", 0),
            "rms_mix_rate": ("float", "RMS 包络混合率", 0.25),
            "unvoiced_protection": ("float", "清辅音保护强度 [0,1]", 0.33),
            "retrieval_blend": ("float", "IVF 检索特征混合率(0 禁用检索)", 0.0),
            "speaker_id": ("int", "多说话人 RVC 检查点的说话人 id", 0),
            "seed": ("int", "随机种子", -1),
        },
    ),
    "meanvc2": (
        "vc",
        "声音转换(MeanVC2)",
        {
            "seed": ("int", "随机种子", 42),
        },
    ),
    "ace_step": (
        "edit",
        "音频编辑/AI 歌曲生成(ACE-Step)",
        {
            "seed": ("int", "随机种子,0 由服务器决定", 0),
        },
    ),
    "sortformer_diar": (
        "diar",
        "说话人分离(检测说话人个数与区间)",
        {
            "speaker_threshold": ("float", "说话人活动概率阈值", 0.5),
            "speaker_min_frames": ("int", "最小解码段长度(帧)", 0),
            "speaker_pad_frames": ("int", "段前后填充(帧)", 0),
        },
    ),
    "mel_band_roformer": (
        "sep",
        "音源分离(人声/伴奏分离)",
        {
            "seed": ("int", "随机种子", -1),
        },
    ),
    "audiosr": (
        "s2s",
        "音频超分辨率(AudioSR,建议接 16kHz 采样率输入)",
        {
            "num_inference_steps": ("int", "DDIM 推理步数", 50),
            "guidance_scale": ("float", "无分类器引导强度", 3.5),
            "ddim_eta": ("float", "DDIM eta(官方默认 1.0)", 1.0),
            "audio_chunk_duration_sec": ("float", "长音频分块时长(秒)", 15.0),
            "audio_chunk_overlap_sec": ("float", "长音频分块重叠(秒)", 2.0),
            "seed": ("int", "随机种子,-1 表示随机", 42),
        },
    ),
    "controlfoley": (
        "sfx",
        "音效生成(ControlFoley,可选视频条件)",
        {
            "duration_sec": ("float", "目标音效时长(秒)", 8.0),
            "num_inference_steps": ("int", "Euler flow 推理步数", 25),
            "guidance_scale": ("float", "无分类器引导强度", 4.5),
            "negative_prompt": ("string", "负向文本提示", ""),
            "seed": ("int", "随机种子", 42),
        },
    ),
    # ---- audio.cpp 新增任务家族(中文档案) ----
    "universr": (
        "s2s",
        "通用音频超分辨率(UniversalSR)",
        {
            "audio_chunk_duration_sec": ("float", "分块时长(秒,0 自动)", 0.0),
            "input_sample_rate": ("int", "输入采样率(留空自动)", 0),
            "sampler_mode": ("combo", "采样器模式", ["euler", "midpoint", "rk4"]),
            "num_inference_steps": ("int", "推理步数", 4),
            "guidance_scale": ("float", "无分类器引导强度", 1.5),
            "seed": ("int", "随机种子", 42),
        },
    ),
    "nemotron_3_diar": (
        "diar",
        "说话人分离(Nemotron-3)",
        {
            "speaker_threshold": ("float", "说话人活动概率阈值", 0.5),
            "speaker_min_frames": ("int", "最小解码段长度(帧)", 0),
            "speaker_pad_frames": ("int", "段前后填充(帧)", 0),
            "return_frame_probabilities": ("bool", "返回帧级概率", False),
        },
    ),
    "liveavatar": (
        "sfx",
        "人像动画生成(LiveAvatar)",
        {
            "reference_image_path": ("string", "参考人像图片(身份/外观条件,必需)", ""),
            "generation_mode": ("combo", "生成模式", ["offline", "liveavatar"]),
            "height": ("int", "输出高度(像素)", 480),
            "width": ("int", "输出宽度(像素)", 832),
            "num_frames": ("int", "输出帧数", 48),
            "num_clips": ("int", "生成片段数", 1),
            "num_inference_steps": ("int", "推理步数", 4),
            "guidance_scale": ("float", "无分类器引导强度", 0.0),
            "seed": ("int", "随机种子", 420),
        },
    ),
    "pulsevad": (
        "vad",
        "语音活动检测(PulseVAD)",
        {
            "threshold": ("float", "语音活动概率阈值", 0.5),
            "hop_size_samples": ("int", "跳步大小(采样点)", 1600),
            "min_speech_duration_ms": ("int", "最小语音段时长(毫秒)", 100),
            "min_silence_duration_ms": ("int", "最小静音段时长(毫秒)", 100),
        },
    ),
    "apollo": (
        "s2s",
        "音频流式处理(Apollo)",
        {
            "audio_chunk_duration_sec": ("float", "分块时长(秒,0 自动)", 0.0),
            "audio_chunk_overlap_sec": ("float", "分块重叠(秒)", 1.0),
            "edge_pad_duration_sec": ("float", "边缘填充时长(秒)", 0.0),
        },
    ),
}


# ---- 自动适配层: 读取 audio.cpp model_specs/*.json ----

def _spec_dir() -> Path | None:
    """返回第一个存在的 model_specs 目录;都不存在返回 None。"""
    for candidate in MODEL_SPEC_DIRS:
        if candidate.is_dir():
            return candidate
    return None


# ---- spec 自动发现的参数黑名单(性能/运行时内部项,不进下拉) ----
# 这些参数由服务器内部消费或属于引擎性能调优,对用户没有直接操作意义;
# 放进下拉只会制造噪音。精确名集合 + 前缀/后缀模式。
SPEC_PARAM_EXACT_EXCLUDE = {
    "mem_saver", "exec_plan", "packed_projections", "stitch_decode",
    "stream_decoder", "attention", "sage_attention", "dit_acceleration",
    "fused_cfg", "filter_nan_logits", "vae_cache_f16",
    "speaker_kv_scale", "zero_speaker_embedding",
    "batch_size", "chunked_decode", "auto_frame_cap",
}
SPEC_PARAM_PREFIX_EXCLUDE = (
    "kv_cache_", "lora", "perf_", "lora_",
)
SPEC_PARAM_SUFFIX_EXCLUDE = (
    "_weight_type", "_graph_arena_mb", "_constant_context_mb",
    "_layerwise", "_layerwise_batch", "cache_interval",
    "first_block_cache_",
)


def _spec_param_excluded(name: str) -> bool:
    """该 spec 参数是否属性能/内部项(不进动态下拉)。"""
    if name in SPEC_PARAM_EXACT_EXCLUDE:
        return True
    for prefix in SPEC_PARAM_PREFIX_EXCLUDE:
        if name.startswith(prefix):
            return True
    for suffix in SPEC_PARAM_SUFFIX_EXCLUDE:
        if name.endswith(suffix) or name.startswith(suffix.rstrip("_")):
            return True
    return False


def _load_spec_families() -> dict[str, dict]:
    """遍历 model_specs/*.json,返回 {family: {tasks, params, languages}}。

    只收录带 options.request 的家族;参数类型按 audio.cpp spec 映射:
        string -> str; int -> int; float -> float; bool -> bool;
        enum  -> 枚举取值列表;其余(string)一律视为字符串。
    性能/内部参数(perf_mode/weight_type/kv_cache_*/lora* 等)按黑名单过滤,
    不进动态下拉(见 SPEC_PARAM_EXACT_EXCLUDE 等)。
    languages 是 spec 声明的支持语言列表(语言下拉数据源)。
    """
    families: dict[str, dict] = {}
    spec_dir = _spec_dir()
    if spec_dir is None:
        return families
    for spec_file in sorted(spec_dir.glob("*.json")):
        try:
            with spec_file.open(encoding="utf-8") as f:
                spec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        family = spec.get("family")
        tasks = spec.get("tasks") or []
        if not family or not isinstance(tasks, list):
            continue
        languages = spec.get("languages") or []
        if not isinstance(languages, list):
            languages = []
        request = (spec.get("options") or {}).get("request") or []
        params: dict[str, tuple] = {}
        for opt in request:
            name = opt.get("name")
            if not name or _spec_param_excluded(name):
                continue
            otype = opt.get("type", "string")
            desc = (opt.get("description") or "")[:80]
            default = opt.get("default")
            if otype == "int":
                params[name] = ("int", desc, default if isinstance(default, int) else 0)
            elif otype == "float":
                params[name] = ("float", desc, default if isinstance(default, (int, float)) else 0.0)
            elif otype == "bool":
                params[name] = ("bool", desc, bool(default))
            elif otype == "enum":
                enums = opt.get("enum") or opt.get("values") or []
                params[name] = ("combo", desc, list(enums) if enums else [])
            else:
                params[name] = ("string", desc, default if isinstance(default, str) else "")
        families[family] = {"tasks": tasks, "params": params, "languages": [str(lang) for lang in languages]}
    return families


# 缓存的 spec 家族(进程内一次性读取;audio.cpp 升级后重启 ComfyUI 即刷新)。
_SPEC_FAMILIES_CACHE: dict | None = None


def spec_families() -> dict[str, dict]:
    global _SPEC_FAMILIES_CACHE
    if _SPEC_FAMILIES_CACHE is None:
        _SPEC_FAMILIES_CACHE = _load_spec_families()
    return _SPEC_FAMILIES_CACHE


# 显式「spec 家族 → 任务类别」强制归类。
# 用于 spec 的 tasks 列表无法准确推断归属的家族(如 vevo2:tasks 含
# 'tts' 但新版 spec category=voice_conversion,实际走 /v1/tasks/run 的
# 声音转换/编辑路线,必须归入任务节点而非 TTS 节点)。
SPEC_TASK_OVERRIDE = {
    "vevo2": "task",
}


def auto_detect_task(family: str) -> str | None:
    """按 spec 的 tasks 推断家族归属任务(找不到返回 None)。

    SPEC_TASK_OVERRIDE 的显式归类优先于 tasks 启发式。
    """
    if family in SPEC_TASK_OVERRIDE:
        return SPEC_TASK_OVERRIDE[family]
    tasks = spec_families().get(family, {}).get("tasks", [])
    if "tts" in tasks or "clone" in tasks or "design" in tasks:
        return "tts"
    if "asr" in tasks or "transcribe" in tasks:
        return "asr"
    # 其余都是音频任务(VC/分离/说话人分离/音乐/MIDI)
    if tasks:
        return "task"
    return None


def spec_languages(family: str) -> list[str]:
    """返回家族支持的 languages 列表(来自 model_specs/*.json)。

    从已缓存的 spec_families() 读取(该缓存一次性扫描目录),
    避免每次下拉构建都重新读文件。
    """
    info = spec_families().get(family)
    if not info:
        return []
    return info.get("languages", [])


# 显式「spec 家族 → 手工档案 id」覆盖关系。
# 手工档案的 key 是「服务器配置的模型 id」(server.json 里的 id),可能与 spec
# 家族名不同(如 muscriptor_small ← family muscriptor)。只有这里列出的 spec
# 家族才由手工档案接管,避免用「前缀」启发式误伤其它合法家族
# (如 chatterbox 与 chatterbox_turbo 是两个独立家族,不应互相覆盖)。
HARDCODED_FAMILY_COVERAGE = {
    "muscriptor": "muscriptor_small",
}


def autodiscovered_families(task: str) -> dict[str, dict]:
    """返回任务类别下的家族参数集(自动发现的),供节点动态构建下拉。

    仅收录 spec 里有参数或已知任务的家族,排除已手工硬编码的(避免重复)。
    覆盖关系 = 同名(HARDCODED_FAMILY_COVERAGE 且 task 匹配)+ 显式映射表。
    task 取值: 'tts' / 'asr' / 'task'。
    """
    hardcoded = {k for k in (TTS_PROFILES if task == "tts" else
                             ASR_PROFILES if task == "asr" else TASK_PROFILES)}

    def spec_family_covered(spec_family: str) -> bool:
        """spec 家族名是否已被手工档案覆盖(同名或列入显式映射表)。"""
        if spec_family in hardcoded:
            return True
        override = HARDCODED_FAMILY_COVERAGE.get(spec_family)
        return bool(override and override in hardcoded)

    result: dict[str, dict] = {}
    for family, info in spec_families().items():
        if spec_family_covered(family):
            continue  # 手工档案优先
        if auto_detect_task(family) == task:
            # 结构: family -> {参数名: (类型, 提示, 默认)}
            result[family] = info["params"]
    return result


def all_family_options(task: str):
    """任务节点下拉的全部选项源:手工档案 + 自动发现的 spec 家族。

    返回 {family: {参数名: spec}},供节点构建 DynamicCombo。

    手工档案家族也会从同名 spec 补齐缺失的请求参数(audio.cpp 升级新增
    参数时自动出现),已有键保留手工定义的中文提示与默认值;spec 的
    顶层字段(voice_ref/reference_text/seed 等)由节点顶层输入承接,跳过。
    """
    manual = TTS_PROFILES if task == "tts" else ASR_PROFILES if task == "asr" else TASK_PROFILES
    manual_params = (
        {k: v[1] for k, v in manual.items()}
        if task in ("tts", "asr")
        else {k: v[2] for k, v in manual.items()}
    )
    merged = dict(manual_params)
    # 手工档案家族:从同名 spec 补齐缺失参数(手工定义优先)
    for family in list(merged):
        spec_params = spec_families().get(family, {}).get("params", {})
        for pname, pspec in spec_params.items():
            merged[family].setdefault(pname, pspec)
    # 自动发现的家族,参数未手工定义则合并进来
    for family, params in autodiscovered_families(task).items():
        merged.setdefault(family, params)
    return merged