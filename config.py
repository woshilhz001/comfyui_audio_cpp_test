"""audio.cpp 服务器地址 / 超时 / 程序路径等集中配置。

所有节点默认读取这里的值;每个节点仍可单独覆盖 host/port,
具备 ComfyUI 工作流的灵活性。集中配置便于后期统一修改。
"""

from __future__ import annotations

from pathlib import Path

# audio.cpp 程序目录(用于定位 model_specs/*.json —— 新模型自动适配的数据源)。
# 这里会优先使用「源码目录」(audio.cpp/audio.cpp),因为 GitHub 上的新模型
# (yue2、sheetsage2、rvc 等)往往先合并进源码的 model_specs,release 稍后才同步。
# 若两个目录都不存在,自动发现的家族数为 0,节点退回手工档案,不会报错。
AUDIO_CPP_DIR = Path(r"D:/ai/llama/audio.cpp")

# model_specs 目录候选顺序:源码目录 > 程序目录。
# 源码目录存在(如 D:/ai/llama/audio.cpp/audio.cpp/model_specs)时优先,
# 以便第一时间获得新增模型的参数定义。
MODEL_SPEC_DIRS = [
    AUDIO_CPP_DIR / "audio.cpp" / "model_specs",
    AUDIO_CPP_DIR / "model_specs",
]

# 默认 audio.cpp 服务器地址。可被每个节点的 host/port 输入覆盖。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8085

# 请求超时(秒)。
REQUEST_TIMEOUT = 600.0
HEALTH_TIMEOUT = 5.0
LIST_TIMEOUT = 10.0
UNLOAD_TIMEOUT = 120.0

# audio.cpp WAV 响应上限(字节)。
MAX_WAV_BYTES = 256 * 1024 * 1024