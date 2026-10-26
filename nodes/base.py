"""audio.cpp 节点共享构建块。

让各节点文件专注于自身的 schema 与执行逻辑。
"""

from __future__ import annotations

from comfy_api.latest import io

from .. import config


def server_address_inputs(include_timeout: bool = True, timeout_default: float = None):
    """返回每个服务器节点共用的 host/port(+timeout)输入。

    每个节点都带 host/port,便于单个工作流连接不同的 audio.cpp 服务器;
    默认值来自 config.py,集中管理。
    """
    inputs = [
        io.String.Input(
            "host",
            display_name="服务器地址",
            default=config.DEFAULT_HOST,
            tooltip="audio.cpp 服务器地址,默认取自 config.py(127.0.0.1)。",
        ),
        io.Int.Input(
            "port",
            display_name="端口",
            default=config.DEFAULT_PORT,
            min=1,
            max=65535,
            tooltip="audio.cpp 服务器端口,默认取自 config.py(8085)。",
        ),
    ]
    if include_timeout:
        inputs.append(
            io.Float.Input(
                "timeout_seconds",
                display_name="超时时间(秒)",
                default=(timeout_default if timeout_default is not None else config.REQUEST_TIMEOUT),
                min=1.0,
                max=3600.0,
                display_mode=io.NumberDisplay.slider,
                tooltip="请求超时时间(秒)。长句或慢速模型可能需要调大。",
            ),
        )
    return inputs