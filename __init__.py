"""audio.cpp 自定义节点的 ComfyUI 扩展注册入口。"""

from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .nodes import (
    AudioCppAudioTask,
    AudioCppServerInfo,
    AudioCppSpeechToText,
    AudioCppTextToSpeech,
    AudioCppUnloadModels,
)
from . import routes as _routes

#: ComfyUI 自动加载本目录下的 js(前端按钮扩展)。
WEB_DIRECTORY = "./web"


class AudioCppExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            AudioCppTextToSpeech,
            AudioCppSpeechToText,
            AudioCppAudioTask,
            AudioCppUnloadModels,
            AudioCppServerInfo,
        ]


def _register_routes() -> None:
    """加载插件时尽力注册 /audionode/* 路由(失败静默)。"""
    try:
        _routes.register_routes()
    except Exception:
        pass


_register_routes()


async def comfy_entrypoint() -> AudioCppExtension:
    return AudioCppExtension()