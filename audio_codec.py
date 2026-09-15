"""Conversion between raw WAV bytes and ComfyUI's native AUDIO dict.

ComfyUI represents audio as {'waveform': torch.Tensor float32 [B, C, T],
'sample_rate': int}. This module owns that conversion and the encoding of a
waveform back to WAV bytes for upload, plus a small tolerance for the legacy
{'audio': np.ndarray [T, C]} shape some packs still emit.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import soundfile as sf
import torch

from .audio_cpp_client import AudioCppError


def wav_bytes_to_audio_dict(wav_bytes: bytes) -> dict[str, Any]:
    """Decode WAV bytes into {'waveform': [B, C, T] float32, 'sample_rate': int}."""
    with sf.SoundFile(io.BytesIO(wav_bytes)) as audio_file:
        sample_rate = int(audio_file.samplerate)
        data = audio_file.read(dtype="float32", always_2d=True)
    waveform = torch.from_numpy(np.ascontiguousarray(data).T).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": sample_rate}


def resample_waveform(waveform: torch.Tensor, src_rate: int, dst_rate: int) -> torch.Tensor:
    """把 [B, C, T] 波形重采样到目标采样率(纯 torch 线性插值,无额外依赖)。

    某些 audio.cpp 模型(如 Sortformer DIAR)期望特定的输入采样率
    (通常 16 kHz),与 TTS 输出(如 24 kHz)不匹配时会报 sample_rate mismatch。
    用户可在任务节点上设置目标采样率后,由此处完成转换。
    """
    if src_rate == dst_rate or dst_rate <= 0 or src_rate <= 0:
        return waveform
    src_len = waveform.shape[-1]
    if src_len < 2:
        return waveform
    # 输出长度 = 输入长度 * dst / src;用线性插值采样
    dst_len = max(1, round(src_len * dst_rate / src_rate))
    src_pos = (torch.arange(dst_len, device=waveform.device).float() + 0.5) * src_rate / dst_rate - 0.5
    src_pos = src_pos.clamp(0, src_len - 1)
    lo = src_pos.long()
    hi = (lo + 1).clamp(max=src_len - 1)
    frac = (src_pos - lo.float()).unsqueeze(0).unsqueeze(0)
    out = waveform[..., lo] * (1 - frac) + waveform[..., hi] * frac
    return out


def audio_dict_to_wav_bytes(audio: dict[str, Any]) -> bytes:
    """Encode a ComfyUI audio dict to WAV bytes (PCM_16)."""
    if "audio" in audio and isinstance(audio["audio"], np.ndarray):
        samples = np.ascontiguousarray(audio["audio"])
        sample_rate = int(audio.get("sample_rate", audio.get("sr", 0)))
        if sample_rate <= 0:
            raise AudioCppError("音频数组输入缺少有效的 sample_rate。")
        return _encode_ndarray_wav(samples, sample_rate)

    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate", 0))
    if not isinstance(waveform, torch.Tensor) or sample_rate <= 0:
        raise AudioCppError("AUDIO 输入必须是 {'waveform': tensor [B,C,T], 'sample_rate': int}")
    if waveform.ndim != 3:
        raise AudioCppError(f"waveform 必须是 [B, C, T],实际形状 {tuple(waveform.shape)}")
    samples = waveform.detach().float().cpu().numpy()
    if samples.shape[0] != 1:
        raise AudioCppError("这里只支持单批(batch=1)的 AUDIO 输入。")
    samples = np.ascontiguousarray(samples[0].T)
    return _encode_ndarray_wav(samples, sample_rate)


def _encode_ndarray_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()