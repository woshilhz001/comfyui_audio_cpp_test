"""ComfyUI extension registration for audio.cpp custom nodes."""

from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .nodes import (
    AudioCppAudioTask,
    AudioCppServerInfo,
    AudioCppSpeechToText,
    AudioCppTextToSpeech,
    AudioCppUnloadModels,
)


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


async def comfy_entrypoint() -> AudioCppExtension:
    return AudioCppExtension()