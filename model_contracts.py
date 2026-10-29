"""模型请求契约层:每个模型家族「怎么调、需要什么输入、哪些参数放顶层」。

与 model_profiles.py(下拉参数长什么样)分离 —— 本模块回答执行期问题:
- 该家族走哪个端点(speech / transcriptions / tasks_run);
- 必须提供哪些顶层输入(audio / voice_ref / text / style);
- 哪些参数从下拉提升到节点顶层(避免同一参数出现两个入口)。

节点执行逻辑只依赖本模块的契约,不关心具体家族。新增模型接入 = 在
TASK_INPUT_REQS / TASK_LIFTS 补一行(或完全自动,靠 spec 的 tasks 类别推断),
节点代码无需改动 —— 这是「好扩展、解耦」的核心边界。
"""

from __future__ import annotations

# ---- 任务类别 -> 中文名 ----
# 用于节点描述、search_aliases 与错误提示。audio.cpp spec 的 tasks 字段
# 取值与中文名的对应关系。未列出的类别原样显示。
TASK_CATEGORY_NAMES = {
    "tts": "语音合成",
    "clone": "声音克隆",
    "design": "音色设计",
    "asr": "语音转文本",
    "transcribe": "语音转文本",
    "vc": "声音转换",
    "svc": "唱歌声音转换",
    "sep": "音源分离",
    "s2s": "音频转音频",
    "diar": "说话人分离",
    "align": "强制对齐",
    "edit": "音频编辑",
    "music": "音乐生成",
    "gen": "音乐生成",
    "midi": "乐谱转写(MIDI/ABC)",
    "sfx": "音效生成",
    "vad": "语音活动检测",
}

# ---- 从下拉隐藏、由节点顶层输入承接的参数名(通用语义) ----
# 这些是 ComfyUI 风格的标准顶层概念,不让它们在下拉里重复出现。
TASK_HIDDEN_FIELDS = {"voice_ref", "target_voice", "language"}
# TTS 家族的顶层字段(与 TTS 节点固定顶层输入一一对应)。
TTS_HIDDEN_FIELDS = {"voice_ref", "reference_text", "language", "seed", "instructions", "voice"}

# ---- 家族 -> 额外从下拉提升到顶层的参数 ----
# 提升后该参数若不填,则不传(服务器用默认)。目前支持两类顶层概念:
#   "style"  -> 顶层 style 输入(音乐/音效的风格描述),回填到 options["style"];
#   "lyrics" -> 顶层 text 输入(歌词),回填到 options["lyrics"]。
# "target_voice" 由顶层 voice_ref 输入经 SERVER_AUDIO_FIELD 映射承接
# (见 nodes/task.py),此处声明保证 should_hide 语义完整。
TASK_LIFTS: dict[str, set[str]] = {
    "yue2": {"style", "lyrics"},
    "heartmula": {"lyrics"},
    "minimax_music3": {"lyrics"},
    "vevo2": {"target_voice"},
    # 其余家族无提升,参数全部留在下拉。
}


def family_lifts(family: str) -> set[str]:
    """返回该家族从下拉提升到顶层的参数名集合。"""
    return TASK_LIFTS.get(family, set())


# ---- 家族 -> 必需顶层输入 ----
# "text"      要求「text」非空(歌词/说明/提示);
# "audio"     要求连接「源音频 audio」;
# "voice_ref" 要求连接「参考音频 voice_ref」(目标声音);
# "style"     要求「style(风格)」非空(音乐模型风格,如 yue2)。
# 未列出的家族不强制(由服务器自行校验,报错信息会透传)。
TASK_INPUT_REQS: dict[str, set[str]] = {
    # 音乐生成:歌词/风格
    "yue2": {"text", "style"},
    "heartmula": {"text"},
    "minimax_music3": {"text"},
    # 乐谱转写:源音频 -> ABC/MIDI(muscriptor 源码强制 audio_input)
    "sheetsage2": {"audio"},
    "muscriptor_small": {"audio"},
    # 声音转换 / 音源分离 / 增强:源音频
    "seed_vc": {"audio", "voice_ref"},
    # vevo2:新版 spec 为声音转换模型(source_audio/target_voice),
    # 源音频走顶层 audio、目标声音走顶层 voice_ref(映射到服务器 target_voice)。
    "vevo2": {"audio", "voice_ref"},
    "rvc": {"audio"},
    "meanvc2": {"audio"},
    "ace_step": {"audio"},
    "sortformer_diar": {"audio"},
    "mel_band_roformer": {"audio"},
    "audiosr": {"audio"},
    "midashenglm_gen": {"text"},
    # 强制对齐:服务器 run() 强制要求「源音频 + 转写文本 text + 语言 language」
    # (build_request_from_json 把顶层 language + text 合成 text_input,缺一即 500
    #  "requires transcript text and language")。text 是转写文本,language 是
    # 其语言代码,均走节点顶层输入。
    "qwen3_forced_aligner": {"audio", "text", "language"},
    "mms_forced_aligner": {"audio", "text", "language"},
    # controlfoley:官方主路径为「提示词纯生成」,源音频/视频均可选,不强制。
    # ---- audio.cpp 新增任务家族 ----
    "universr": {"audio"},
    "apollo": {"audio"},
    "pulsevad": {"audio"},
    "nemotron_3_diar": {"audio"},
}


def family_required_inputs(family: str) -> set[str]:
    """返回该家族必需的顶层输入集合(audio / voice_ref / text)。"""
    return TASK_INPUT_REQS.get(family, set())


def should_hide(family: str, param_name: str) -> bool:
    """该参数是否应在模型下拉里隐藏(交给顶层输入承接)。"""
    return param_name in TASK_HIDDEN_FIELDS or param_name in family_lifts(family)


def lifted_value(lift: str, text: str, style: str) -> str | None:
    """把提升的顶层输入回填成 options 值;空输入返回 None(不传)。

    lift 取值: "lyrics"(取歌词 text)/ "style"(取风格 style)。
    """
    if lift == "lyrics":
        return text.strip() or None
    if lift == "style":
        return style.strip() or None
    return None


def task_category_cn(tasks: list[str] | None) -> str:
    """spec 的 tasks 列表 -> 中文类别名(找不到返回第一个原始任务名)。"""
    if not tasks:
        return "音频任务"
    for task in tasks:
        if task in TASK_CATEGORY_NAMES:
            return TASK_CATEGORY_NAMES[task]
    return tasks[0]


# AudioCppError 异常类型重导出,方便节点层统一使用。
from .audio_cpp_client import AudioCppError  # noqa: E402  (保持模块内聚)