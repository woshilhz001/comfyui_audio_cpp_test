"""Shared building blocks for the audio.cpp nodes.

Keeps the per-node files focused on their own schema and execution logic.
"""

from __future__ import annotations

from comfy_api.latest import io

from .. import config


def server_address_inputs(include_timeout: bool = True, timeout_default: float = None):
    """Return the shared host/port (+timeout) inputs used by every server node.

    每个节点都带 host/port,便于单个工作流连接不同的 audio.cpp 服务器;
    默认值来自 config.py,集中管理。
    """
    inputs = [
        io.String.Input(
            "host",
            display_name="host",
            default=config.DEFAULT_HOST,
            tooltip="audio.cpp 服务器地址,默认取自 config.py(127.0.0.1)。",
        ),
        io.Int.Input(
            "port",
            display_name="port",
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
                display_name="timeout_seconds",
                default=(timeout_default if timeout_default is not None else config.REQUEST_TIMEOUT),
                min=1.0,
                max=3600.0,
                display_mode=io.NumberDisplay.slider,
                tooltip="请求超时时间(秒)。长句或慢速模型可能需要调大。",
            ),
        )
    return inputs