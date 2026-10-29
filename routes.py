"""AudioCpp 前端按钮路由:浏览器无法直连 audio.cpp 服务器(无 CORS),
「卸载显存」按钮经本路由由 ComfyUI 后端代理转发。

- POST /audionode/unload_models   请求体 {"host", "port", "mode", "model_ids"}
  转发到 audio.cpp 的 /v1/tasks/unload_models 或 /v1/tasks/unload_all_models,
  返回 {"ok": true, "unloaded": [...], "report": "..."};
  服务器不可达/拒绝时返回 502 {"ok": false, "error": "..."}。

mode 取值与「AudioCpp 卸载模型(释放显存)」节点一致:
  unload_all / unload_selected / check_only
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from .audio_cpp_client import (
    AudioCppError,
    build_base_url,
    check_server,
    list_models,
    unload_all_models,
    unload_models,
)

logger = logging.getLogger("audiocpp.routes")

UNLOAD_MODES = ("unload_all", "unload_selected", "check_only")


async def unload_models_handler(request: web.Request) -> web.Response:
    """POST /audionode/unload_models —— 代理 audio.cpp 卸载端点。

    请求体字段(均可缺省,用 config 默认值):
      host / port / mode / model_ids(逗号分隔字符串)
    """
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}

    host = str(body.get("host") or "")
    port = body.get("port") or 0
    mode = str(body.get("mode") or "unload_all")
    model_ids = str(body.get("model_ids") or "")

    if mode not in UNLOAD_MODES:
        return web.json_response(
            {"ok": False, "error": f"未知的卸载模式: {mode}"}, status=400
        )
    if mode == "unload_selected" and not model_ids.strip():
        return web.json_response(
            {"ok": False, "error": "unload_selected 模式需要在 model_ids 填写至少一个模型 id"},
            status=400,
        )

    try:
        result = await asyncio.to_thread(_do_unload, host, int(port), mode, model_ids)
    except AudioCppError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)
    except Exception as exc:
        logger.warning("AudioCpp 卸载代理失败: %s", exc)
        return web.json_response(
            {"ok": False, "error": f"卸载请求处理失败: {exc}"}, status=502
        )
    return web.json_response(result)


def _do_unload(host: str, port: int, mode: str, model_ids: str) -> dict[str, Any]:
    """同步执行卸载(在 to_thread 里跑,不阻塞事件循环)。"""
    base_url = build_base_url(host, port)
    check_server(base_url)
    models_before = list_models(base_url)
    loaded_before = [str(m["id"]) for m in models_before if m.get("loaded")]

    unloaded: list[str] = []
    if mode == "unload_all":
        unloaded = unload_all_models(base_url)
    elif mode == "unload_selected":
        id_list = [i.strip() for i in model_ids.split(",") if i.strip()]
        unloaded = unload_models(base_url, id_list)
    else:  # check_only
        pass

    now_loaded = [str(m["id"]) for m in list_models(base_url) if m.get("loaded")]
    report = (
        f"卸载前已加载 {len(loaded_before)} 个\n"
        f"本次卸载 {len(unloaded)} 个: {', '.join(unloaded) if unloaded else '无'}\n"
        f"卸载后仍加载 {len(now_loaded)} 个"
    )
    return {"ok": True, "unloaded": unloaded, "report": report}


_registered = False


def register_routes() -> None:
    """注册 /audionode/* 路由(幂等,失败静默不阻塞启动)。

    与 ArcReel 相同:ComfyUI 0.42 的 PromptServer 没有 route() 方法,
    用独立 RouteTableDef 热挂载进 app.router。
    """
    global _registered
    if _registered:
        return
    try:
        from server import PromptServer
    except ImportError:
        return
    server = getattr(PromptServer, "instance", None)
    if server is None:
        return

    routes = web.RouteTableDef()

    @routes.post("/audionode/unload_models")
    async def _unload(request: web.Request) -> web.Response:
        return await unload_models_handler(request)

    app = getattr(server, "app", None)
    if app is not None:
        try:
            app.router.add_routes(routes)
        except Exception as exc:
            logger.warning(
                "AudioCpp 路由热挂载失败(插件在服务器启动前加载时此步骤冗余,可忽略): %s", exc
            )
    _registered = True
