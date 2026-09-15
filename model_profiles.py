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
            "language": ("string", "语言代码(english/en/zh/ja/ko/...)", "english"),
        },
    ),
    "qwen3_tts_base": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径(必需)", ""),
            "reference_text": ("string", "参考音频对应的文本转写(必需)", ""),
            "language": ("string", "语言代码", "english"),
            "seed": ("int", "随机种子", -1),
        },
    ),
    "qwen3_tts_customvoice": (
        "tts",
        {
            "voice": ("string", "内置音色 id,如 Vivian、Ryan", "Vivian"),
            "language": ("string", "语言代码", "english"),
            "seed": ("int", "随机种子", -1),
        },
    ),
    "cosyvoice3": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "instruction": ("string", "指令文本(instruct 模式)", ""),
            "template_name": ("combo", "CosyVoice3 请求模板", ["zero_shot", "cross_lingual", "instruct"]),
            "text_chunk_size": ("int", "长文本分块大小", 600),
            "text_chunk_mode": ("combo", "分块模式", ["default", "tag_aware", "japanese", "endline"]),
            "max_tokens": ("int", "最大生成 token 数", 1600),
            "min_tokens": ("int", "最小生成 token 数", 0),
            "top_k": ("int", "Top-K 采样", 25),
            "num_inference_steps": ("int", "Flow 解码步数", 10),
            "seed": ("int", "随机种子", 1986),
        },
    ),
    "omnivoice": (
        "tts",
        {
            "voice": ("string", "内置音色 id(OmniVoice 支持的内置语音)", ""),
            "seed": ("int", "随机种子", -1),
        },
    ),
    "chatterbox_turbo": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "max_tokens": ("int", "最大生成 token 数", 0),
            "text_chunk_size": ("int", "长文本分块大小", 600),
            "text_chunk_mode": ("combo", "分块模式", ["default", "tag_aware", "japanese", "endline"]),
            "seed": ("int", "随机种子", -1),
        },
    ),
    "vevo2": (
        "tts",
        {
            "voice_ref": ("path", "服务器端克隆参考音频 WAV 路径", ""),
            "reference_text": ("string", "参考音频对应的文本转写", ""),
            "duration_sec": ("float", "生成时长(秒),0 为自动", 0.0),
            "seed": ("int", "随机种子", -1),
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
}

# 音频任务家族: family -> (task, 任务中文名, {参数名: spec})
TASK_PROFILES = {
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
            "seed": ("int", "随机种子", -1),
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
}


# ---- 自动适配层: 读取 audio.cpp model_specs/*.json ----

def _spec_dir() -> Path | None:
    """返回第一个存在的 model_specs 目录;都不存在返回 None。"""
    for candidate in MODEL_SPEC_DIRS:
        if candidate.is_dir():
            return candidate
    return None


def _load_spec_families() -> dict[str, dict]:
    """遍历 model_specs/*.json,返回 {family: {tasks, params, languages}}。

    只收录带 options.request 的家族;参数类型按 audio.cpp spec 映射:
        string -> str; int -> int; float -> float; bool -> bool;
        enum  -> 枚举取值列表;其余(string)一律视为字符串。
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


def auto_detect_task(family: str) -> str | None:
    """按 spec 的 tasks 推断家族归属任务(找不到返回 None)。"""
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


def autodiscovered_families(task: str) -> dict[str, dict]:
    """返回任务类别下的家族参数集(自动发现的),供节点动态构建下拉。

    仅收录 spec 里有参数或已知任务的家族,排除已手工硬编码的(避免重复)。
    task 取值: 'tts' / 'asr' / 'task'。
    """
    hardcoded = {k for k in (TTS_PROFILES if task == "tts" else
                             ASR_PROFILES if task == "asr" else TASK_PROFILES)}
    result: dict[str, dict] = {}
    for family, info in spec_families().items():
        if family in hardcoded:
            continue  # 手工档案优先
        if auto_detect_task(family) == task:
            # 结构: family -> {参数名: (类型, 提示, 默认)}
            result[family] = info["params"]
    return result


def all_family_options(task: str):
    """任务节点下拉的全部选项源:手工档案 + 自动发现的 spec 家族。

    返回 {family: {参数名: spec}},供节点构建 DynamicCombo。
    """
    if task == "tts":
        merged = {k: v[1] for k, v in TTS_PROFILES.items()}
    elif task == "asr":
        merged = {k: v[1] for k, v in ASR_PROFILES.items()}
    else:
        merged = {k: v[2] for k, v in TASK_PROFILES.items()}
    # 自动发现的家族,参数未手工定义则合并进来
    for family, params in autodiscovered_families(task).items():
        merged.setdefault(family, params)
    return merged