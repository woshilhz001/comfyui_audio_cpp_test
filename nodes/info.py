"""AudioCppServerInfo: 服务器状态与模型加载情况诊断节点。

只读操作,不会修改服务器状态。用于排查 audio.cpp 服务器是否就绪、
当前加载了哪些模型、后端是什么(用于确认显存分配情况)。
"""

from __future__ import annotations

import json

from comfy_api.latest import io

from ..audio_cpp_client import build_base_url, check_server, list_models
from .base import server_address_inputs


class AudioCppServerInfo(io.ComfyNode):
    """查看 audio.cpp 服务器状态与已加载模型。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppServerInfo",
            display_name="AudioCpp 服务器信息",
            category="audio.cpp",
            description=(
                "只读诊断节点:查看 audio.cpp 服务器的连接状态、后端类型、\n"
                "配置的模型数量,以及当前已加载(占用显存/内存)的模型清单。\n"
                "不会修改服务器状态。"
            ),
            search_aliases=["服务器状态", "模型列表", "health", "audio.cpp 状态"],
            inputs=server_address_inputs(include_timeout=False),
            outputs=[
                io.String.Output(
                    "report",
                    display_name="状态报告",
                    tooltip="人类可读的状态摘要(status/backend/loaded)。",
                ),
                io.String.Output(
                    "models_json",
                    display_name="模型 JSON",
                    tooltip="全部配置模型的 JSON 文本(含 loaded 字段)。",
                ),
            ],
        )

    @classmethod
    def execute(cls, host: str, port: int) -> io.NodeOutput:
        base_url = build_base_url(host, port)
        health = check_server(base_url)
        models = list_models(base_url)
        loaded = [f"{m.get('id')} ({m.get('task')})" for m in models if m.get("loaded")]
        report = (
            f"状态: {health.get('status')}\n"
            f"后端: {health.get('backend')}\n"
            f"配置模型数: {health.get('models')}\n"
            f"已加载(占用显存/内存): {len(loaded)}\n"
            + ("\n".join(f"  - {name}" for name in loaded) if loaded else "  (无)")
        )
        return io.NodeOutput(report, json.dumps(models, indent=2, ensure_ascii=False))