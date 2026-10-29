"""audio.cpp 服务器(audiocpp_server)的 HTTP 客户端。

封装本节点包用到的端点:健康检查、模型清单、TTS、ASR、音色列表与模型卸载。
本模块不依赖 ComfyUI,只操作普通值(bytes / dict / list),可独立测试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from . import config
from .model_profiles import spec_families


class AudioCppError(RuntimeError):
    """服务器不可达或拒绝请求时抛出。"""


def is_meaningful_option_value(name: str, value: Any) -> bool:
    """该 option 值是否应传给服务器(节点下拉 → options 的过滤规则)。

    不传(返回 False)的值:
    - 空字符串 / None(未填写);
    - 数值 0:服务器对 min≥1 的参数(如 text_chunk_size)传 0 会 500,
      guidance_scale 等要求正数的参数传 0.0 会 500("must be positive");
      不传则服务器用内置默认。
    - seed 负值(-1 表示由服务器随机):直接传会被
      "seed must be an unsigned integer" 拒绝(HTTP 500)。
    """
    if value in ("", None):
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 0:
            return False
        if name == "seed" and value < 0:
            return False
    return True


def build_base_url(host: str, port: int) -> str:
    host = (host or config.DEFAULT_HOST).strip().rstrip("/")
    return f"http://{host}:{int(port)}"


def check_server(base_url: str, timeout: float = config.HEALTH_TIMEOUT) -> dict[str, Any]:
    """返回 /health 负载;服务器不可达时抛 AudioCppError。"""
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


def list_models(
    base_url: str,
    timeout: float = config.LIST_TIMEOUT,
    *,
    include_session_options: bool = False,
    include_params: bool = False,
) -> list[dict[str, Any]]:
    """返回 /v1/models 配置的模型清单。

    新版服务器条目含 loaded / configured 状态字段;
    include_params=True 时额外附带该模型的请求参数定义(params)。
    """
    query = ""
    if include_params:
        query = "?include_params=true"
    elif include_session_options:
        query = "?include_session_options=true"
    try:
        response = requests.get(f"{base_url}/v1/models{query}", timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"无法连接 audio.cpp 服务器 {base_url}: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(
            f"audio.cpp /v1/models 返回 HTTP {response.status_code}: {_error_message(response)}"
        )
    return list(_json_or_error(response, "audio.cpp /v1/models").get("data", []))


def load_model(base_url: str, payload: dict[str, Any], timeout: float = config.REQUEST_TIMEOUT) -> dict[str, Any]:
    """运行时注册(并加载)一个模型:POST /v1/models/load。

    需要服务器以 --ui-management 启动;返回 {"id", "loaded", "reconfigured"}。
    该请求是同步的(服务器完成加载后才返回),大模型需要较长时间。
    """
    try:
        response = requests.post(f"{base_url}/v1/models/load", json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"注册模型请求失败: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(f"注册模型失败 (HTTP {response.status_code}): {_error_message(response)}")
    data = _json_or_error(response, "audio.cpp 模型注册接口")
    return data if isinstance(data, dict) else {}


def default_model_path(family: str) -> str:
    """新下载模型的兜底相对路径:models/<family>(仅用于报错信息展示)。

    实际路径由 _pick_model_dir 在 models 根目录下按家族名自动定位,
    因为 WebUI 下载的目录名带变体后缀(如 FireRedTTS3-Instruct-GGUF)。
    """
    return f"models/{family}"


def _find_model_dirs(models_root: Path) -> list[Path]:
    """列出 models_root 下的一级子目录(不存在则返回空列表)。"""
    if not models_root.is_dir():
        return []
    try:
        return sorted(p for p in models_root.iterdir() if p.is_dir())
    except OSError:
        return []


def _norm_name(name: str) -> str:
    """归一化目录名/family 名:小写 + 下划线转连字符。

    family 命名用下划线(如 breeze_tts),WebUI 下载目录名用连字符
    (如 Breeze-TTS-2-GGUF),两边归一化后再比较才能命中。
    """
    return name.lower().replace("_", "-")


def _pick_model_dir(models_root: Path, family: str) -> Path | None:
    """在 models_root 下定位 family 对应的模型目录。

    匹配优先级(目录名与 family 均按 _norm_name 归一化后比较,
    即小写且下划线/连字符等价):
    1. 完全等于 family;
    2. 等于 family + "-gguf"(包安装惯例,如 models/qwen3-tts-gguf);
    3. 前缀 family + "-"(WebUI 下载惯例,如 FireRedTTS3-Instruct-GGUF、
       Breeze-TTS-2-GGUF);
    4. 任意位置包含 family;
    5. 去分隔符后包含(family 是缩写写法,如 seed_vc → SeedVC-MLX-GGUF)。
    同组多个候选时优先含 .gguf 文件的目录,其次名字更短的。
    """
    name = _norm_name(family)
    candidates = _find_model_dirs(models_root)

    def _group(pred) -> list[Path]:
        return [c for c in candidates if pred(_norm_name(c.name))]

    def _rank(group: list[Path]) -> Path | None:
        if not group:
            return None

        def sort_key(p: Path):
            has_gguf = 0
            try:
                has_gguf = any(f.suffix.lower() == ".gguf" for f in p.iterdir() if f.is_file())
            except OSError:
                pass
            return (-int(has_gguf), len(p.name))

        return sorted(group, key=sort_key)[0]

    for group in (
        _group(lambda n: n == name),
        _group(lambda n: n == f"{name}-gguf"),
        _group(lambda n: n.startswith(f"{name}-")),
        _group(lambda n: name in n),
        # family 是缩写写法(如 seed_vc)时,去分隔符后包含:
        # "seedvc" in "seedvcmlxgguf" → SeedVC-MLX-GGUF
        _group(lambda n: name.replace("-", "") in n.replace("-", "")),
    ):
        picked = _rank(group)
        if picked is not None:
            return picked
    return None


def _resolve_server_models_root(base_url: str) -> Path:
    """服务器模型根目录:本机部署直接用 config.AUDIO_CPP_DIR/models
    (与服务器的 request_base 一致);目录不存在时(远端部署)查
    GET /v1/ui/models-root,接口不可用则退回本地默认。
    """
    local_root = config.AUDIO_CPP_DIR / "models"
    if local_root.is_dir():
        return local_root
    try:
        response = requests.get(f"{base_url}/v1/ui/models-root", timeout=config.LIST_TIMEOUT)
        if response.status_code == 200:
            data = _json_or_error(response, "audio.cpp 模型根目录接口")
            root_text = data.get("models_root") if isinstance(data, dict) else None
            if root_text:
                return Path(root_text)
    except (requests.RequestException, AudioCppError):
        pass
    return local_root


def ensure_model_registered(
    base_url: str,
    family: str,
    path: str | None = None,
    *,
    task: str | None = None,
    timeout: float = config.REQUEST_TIMEOUT,
) -> str:
    """确保 family 已注册到服务器,返回该模型的服务器 id。

    audio.cpp 的模型注册表(server.json)与模型文件是解耦的:新下载/放入
    models/ 目录的模型不会自动出现在 /v1/models,首次请求即 500
    "unknown model id"。此函数用 /v1/models/load 运行时补注册(需要
    --ui-management,本机启动脚本已开启),使下载新模型后节点无需改配置。

    - 已注册: 直接返回 id(幂等,不做任何变更);
    - 未注册: 从本地 model_specs 读 tasks 推断任务类别,构造
      {id, path, family, task, mode: "offline"} 调 /v1/models/load,
      注册后复查 /v1/models 确认生效;
    - path 缺省为 models/<family>,可传绝对/相对路径覆盖;
    - 失败时抛带中文指引的 AudioCppError(区分 403 未开 --ui-management
      与 4xx 模型文件缺失)。
    """
    for entry in list_models(base_url, timeout=min(timeout, config.LIST_TIMEOUT)):
        if entry.get("id") == family:
            return str(entry["id"])

    spec_info = spec_families().get(family) or {}
    tasks = [str(t) for t in spec_info.get("tasks", [])]
    task_value = task or (tasks[0] if tasks else "tts")

    if path is None:
        root = _resolve_server_models_root(base_url)
        picked = _pick_model_dir(root, family)
        if picked is None:
            raise AudioCppError(
                f"自动注册模型 {family} 失败:在服务器 models 目录 {root} 下"
                f"找不到以 {family} 命名的模型目录。\n"
                f"请确认模型已下载,或给节点传入明确的模型路径"
                f"(默认预期 {default_model_path(family)})。"
            )
        try:
            path = picked.relative_to(root.parent).as_posix()
        except ValueError:
            path = picked.as_posix()

    payload = {
        "id": family,
        "path": path,
        "family": family,
        "task": task_value,
        "mode": "offline",
    }
    try:
        load_model(base_url, payload, timeout=timeout)
    except AudioCppError as exc:
        if "403" in str(exc):
            raise AudioCppError(
                f"自动注册模型 {family} 被服务器拒绝:服务器未开启动态模型管理。\n"
                "请用 --ui-management 启动 audiocpp_server(本机启动脚本已包含该参数)。"
            ) from exc
        raise AudioCppError(
            f"自动注册模型 {family} 失败(路径 {path}):\n{exc}\n"
            "请确认模型文件已存在于服务器 models/ 目录。"
        ) from exc

    # 复查注册生效(服务器可能注册成功但加载失败,条目仍在列表里)
    ids = [str(e.get("id")) for e in list_models(base_url, timeout=min(timeout, config.LIST_TIMEOUT))]
    if family not in ids:
        raise AudioCppError(
            f"注册模型 {family} 后复查 /v1/models 仍未出现,请检查服务器日志。"
        )
    return family


def list_voices(base_url: str, model: str | None = None, timeout: float = config.LIST_TIMEOUT) -> list[str]:
    """返回 GET /v1/audio/voices 的音色列表。

    新版服务器按模型返回:voice 预设名 + <模型目录>/embeddings/*.safetensors
    的 stem + 服务器 --voice-dir 里的 wav stem(已去重排序)。
    不指定 model 时(多模型服务器)返回空列表;单模型服务器返回该模型列表。
    """
    query = f"?model={model}" if model else ""
    try:
        response = requests.get(f"{base_url}/v1/audio/voices{query}", timeout=timeout)
    except requests.RequestException as exc:
        raise AudioCppError(f"无法连接 audio.cpp 服务器 {base_url}: {exc}") from exc
    if response.status_code != 200:
        raise AudioCppError(
            f"audio.cpp /v1/audio/voices 返回 HTTP {response.status_code}: {_error_message(response)}"
        )
    data = _json_or_error(response, "audio.cpp /v1/audio/voices")
    voices = data.get("voices", []) if isinstance(data, dict) else []
    return [str(v) for v in voices]


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
    """POST /v1/audio/speech 并返回原始音频字节(默认 WAV)。

    extra_options 合并进顶层 "options" 对象,与服务器的 build_speech_request
    (options_from_object(body["options"]))一致。只接受特定参数的模型族会对
    未知参数报错,并以带服务器消息的 AudioCppError 形式浮出。
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
    """POST /v1/audio/transcriptions(multipart)并返回 JSON 负载。

    上传的音频必须是 WAV;服务器端 mp3/flac 解码是可选的,所以调用方先转 WAV。
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
    """卸载指定模型(POST /v1/tasks/unload_models)。"""
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
    """卸载全部常驻模型(POST /v1/tasks/unload_all_models)。"""
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
    language: str | None = None,
    audio_wav: bytes | None = None,
    voice_ref_path: str | None = None,
    voice_ref_field: str = "voice_ref",
    options: dict[str, Any] | None = None,
    timeout: float = config.REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """POST /v1/tasks/run 返回完整 JSON 结果。

    这是通用任务端点:接受顶层 'text'(字符串)、'language'(语言代码)与
    'audio'/'voice_ref'(服务器端文件路径)。ComfyUI 音频先经 /v1/ui/upload 上传
    (见 _stage_audio),再把返回的临时路径传进来,与 build_request_from_json 的
    resolve_case_path 解析方式一致。

    text + language 一起构成服务器端 text_input(Transcript{text, language}):
    build_request_from_json 从顶层 'language' 读取语言并与顶层 'text' 合成,
    两者缺一时强制对齐模型会 500 "requires transcript text and language"。

    voice_ref_field 指定参考音频在请求体里使用的顶层字段名:默认
    'voice_ref'(Seed-VC 等);vevo2 等新模型要求 'target_voice'
    (build_request_from_json 会把它映射进 options.target_voice)。

    返回解析后的 JSON 结果字典;服务器错误以带服务器消息的 AudioCppError 浮出。
    """
    path_options: dict[str, Any] = {}
    if audio_wav:
        path_options["audio"] = _stage_audio(base_url, audio_wav, "input.wav", timeout)
    if voice_ref_path:
        path_options[voice_ref_field] = voice_ref_path

    payload: dict[str, Any] = {"model": model}
    if text:
        payload["text"] = text
    if language:
        payload["language"] = language
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
    """把原始 WAV 字节上传到 /v1/ui/upload,返回服务器端路径。

    服务器把请求体当作原始字节读取,文件名取自 'x-audiocpp-filename' 头
    (非 multipart),返回绝对临时路径 {'path': ...}。/v1/tasks/run 只把
    'audio'/'voice_ref' 当作服务器路径(不接受 base64),所以未以
    --ui/--ui-management 启动的服务器不能接受上传音频;遇到时明确报错。
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