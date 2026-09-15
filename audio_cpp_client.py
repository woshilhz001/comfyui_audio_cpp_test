"""HTTP client for the audio.cpp server (audiocpp_server).

Wraps the endpoints the node pack uses: health, model inventory, TTS, ASR,
voice listing, and model unloading. This module owns no ComfyUI concepts; it
works on plain values (bytes, dicts, lists) so it can be tested standalone.
"""

from __future__ import annotations

from typing import Any

import requests

from . import config


class AudioCppError(RuntimeError):
    """Raised when the server is unreachable or rejects a request."""


def build_base_url(host: str, port: int) -> str:
    host = (host or config.DEFAULT_HOST).strip().rstrip("/")
    return f"http://{host}:{int(port)}"


def check_server(base_url: str, timeout: float = config.HEALTH_TIMEOUT) -> dict[str, Any]:
    """Return the /health payload, raising AudioCppError when unreachable."""
    try:
        response = requests.get(f"{base_url}/health", timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(
            f"无法连接 audio.cpp 服务器 {base_url}: {exc}。"
            "请先启动 audiocpp_server(例如 audiocpp_server --config server.json)。"
        ) from exc
    if response.status_code != 200:
        raise AudioCppError(
            f"audio.cpp /health 返回 HTTP {response.status_code}: {_error_message(response)}"
        )
    return _json_or_error(response, "audio.cpp /health")


def list_models(base_url: str, timeout: float = config.LIST_TIMEOUT) -> list[dict[str, Any]]:
    """Return the configured models from /v1/models."""
    try:
        response = requests.get(f"{base_url}/v1/models", timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"无法连接 audio.cpp 服务器 {base_url}: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(
            f"audio.cpp /v1/models 返回 HTTP {response.status_code}: {_error_message(response)}"
        )
    return list(_json_or_error(response, "audio.cpp /v1/models").get("data", []))


def speech_synthesize(
    base_url: str,
    model: str,
    text: str,
    *,
    voice: str | None = None,
    voice_ref_path: str | None = None,
    reference_text: str | None = None,
    instructions: str | None = None,
    language: str | None = None,
    speed: float | None = None,
    seed: int | None = None,
    extra_options: dict[str, Any] | None = None,
    timeout: float = config.REQUEST_TIMEOUT,
) -> bytes:
    """POST /v1/audio/speech and return raw audio bytes (WAV by default).

    extra_options are merged under the top-level "options" object, matching the
    server's build_speech_request (options_from_object(body["options"])). Model
    families that only accept specific options reject unknown ones with a clear
    server error, which surfaces as AudioCppError with the server message.
    """
    payload: dict[str, Any] = {"model": model, "input": text}
    if voice:
        payload["voice"] = voice
    if voice_ref_path:
        payload["voice_ref"] = {"type": "path", "path": voice_ref_path}
    if reference_text:
        payload["reference_text"] = reference_text
    if instructions:
        payload["instructions"] = instructions
    if language:
        payload["language"] = language
    if speed is not None:
        payload["speed"] = speed
    if seed is not None:
        payload["seed"] = seed
    if extra_options:
        payload["options"] = extra_options
    try:
        response = requests.post(f"{base_url}/v1/audio/speech", json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"TTS 请求失败: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(f"TTS 失败 (HTTP {response.status_code}): {_error_message(response)}")
    if len(response.content) > config.MAX_WAV_BYTES:
        raise AudioCppError(f"TTS 响应过大 ({len(response.content)} bytes),已超过安全上限。")
    return response.content


def transcribe(
    base_url: str,
    model: str,
    wav_bytes: bytes,
    *,
    language: str | None = None,
    prompt: str | None = None,
    timeout: float = config.REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """POST /v1/audio/transcriptions (multipart) and return the JSON payload.

    The uploaded audio must be WAV; MP3/flac decoding is optional on the server
    side, so the caller converts to WAV beforehand.
    """
    fields: dict[str, str] = {"model": model}
    if language:
        fields["language"] = language
    if prompt:
        fields["prompt"] = prompt
    try:
        response = requests.post(
            f"{base_url}/v1/audio/transcriptions",
            data=fields,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AudioCppError(f"ASR 请求失败: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(f"ASR 失败 (HTTP {response.status_code}): {_error_message(response)}")
    return _json_or_error(response, "ASR 接口")


def unload_models(base_url: str, model_ids: list[str], timeout: float = config.UNLOAD_TIMEOUT) -> list[str]:
    """Unload specific models (POST /v1/tasks/unload_models)."""
    try:
        response = requests.post(
            f"{base_url}/v1/tasks/unload_models", json={"model_ids": model_ids}, timeout=timeout
        )
    except requests.RequestException as exc:
        raise AudioCppError(f"卸载模型请求失败: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(f"卸载模型失败 (HTTP {response.status_code}): {_error_message(response)}")
    return list(_json_or_error(response, "卸载模型接口").get("unloaded", []))


def unload_all_models(base_url: str, timeout: float = config.UNLOAD_TIMEOUT) -> list[str]:
    """Unload every resident model (POST /v1/tasks/unload_all_models)."""
    try:
        response = requests.post(f"{base_url}/v1/tasks/unload_all_models", timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"全量卸载请求失败: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(f"全量卸载失败 (HTTP {response.status_code}): {_error_message(response)}")
    return list(_json_or_error(response, "全量卸载接口").get("unloaded", []))


def _error_message(response: requests.Response) -> str:
    """从错误响应中提取服务器返回的错误消息,兼容非 JSON 响应。"""
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return str(data) if data else response.text[:300]


def _json_or_error(response: requests.Response, what: str) -> Any:
    """解析响应 JSON;服务器返回非 JSON(HTML 错误页等)时给出明确报错。"""
    try:
        return response.json()
    except ValueError as exc:
        raise AudioCppError(
            f"{what}返回了非 JSON 响应 (HTTP {response.status_code}): {response.text[:300]}"
        ) from exc


def task_run(
    base_url: str,
    model: str,
    *,
    text: str | None = None,
    audio_wav: bytes | None = None,
    voice_ref_path: str | None = None,
    options: dict[str, Any] | None = None,
    timeout: float = config.REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """POST /v1/tasks/run returning the full JSON result.

    This is the generic task endpoint: it accepts 'text' (string) and 'audio' /
    'voice_ref' as server-side file paths. ComfyUI audio is uploaded via
    /v1/ui/upload first (see _stage_audio) and the returned temp path is passed
    here, matching build_request_from_json's resolve_case_path resolution.

    Returns the parsed JSON result dict; server errors surface as AudioCppError
    with the server's message.
    """
    path_options: dict[str, Any] = {}
    if audio_wav:
        path_options["audio"] = _stage_audio(base_url, audio_wav, "input.wav", timeout)
    if voice_ref_path:
        path_options["voice_ref"] = voice_ref_path

    payload: dict[str, Any] = {"model": model}
    if text:
        payload["text"] = text
    if options:
        payload["options"] = options
    payload.update(path_options)

    try:
        response = requests.post(f"{base_url}/v1/tasks/run", json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"任务请求失败: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(f"任务失败 (HTTP {response.status_code}): {_error_message(response)}")
    return _json_or_error(response, "audio.cpp 任务接口")


def _stage_audio(base_url: str, wav_bytes: bytes, filename: str, timeout: float) -> str:
    """Upload raw WAV bytes to /v1/ui/upload and return the server-side path.

    The server reads the body as raw bytes and the filename from the
    'x-audiocpp-filename' header (not multipart), returning {'path': ...} with
    an absolute temp path. /v1/tasks/run reads 'audio'/'voice_ref' as server
    paths only (no base64), so a server without --ui/--ui-management cannot
    accept uploaded audio; raise with a clear message in that case.
    """
    try:
        upload = requests.post(
            f"{base_url}/v1/ui/upload",
            data=wav_bytes,
            headers={"Content-Type": "audio/wav", "x-audiocpp-filename": filename},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AudioCppError(f"上传音频到 audio.cpp 服务器失败: {exc}") from exc
    if upload.status_code != 200:
        raise AudioCppError(
            f"上传音频被服务器拒绝 (HTTP {upload.status_code}): {_error_message(upload)}。"
            "需要上传音频的任务要求服务器以 --ui(或 --ui-management)启动,"
            "请检查 audiocpp_server 的启动参数。"
        )
    try:
        data = upload.json()
    except ValueError as exc:
        raise AudioCppError("上传音频后服务器返回了非 JSON(可能未开启 --ui 的上传接口)。") from exc
    path = data.get("path") if isinstance(data, dict) else None
    if not path:
        raise AudioCppError(f"上传音频响应缺少 path 字段: {data!r}")
    return path