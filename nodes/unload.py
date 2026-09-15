"""AudioCppUnloadModels: 卸载模型、完全释放显存/内存的节点。

调用 audio.cpp 服务器的 /v1/tasks/unload_models 和 /v1/tasks/unload_all_models。
卸载会立即释放 GPU 显存和 CPU 内存,已加载模型会被逐出,
下次使用对应模型时需要重新加载(耗时几秒到几十秒)。

设计理念(简洁、不膨胀):
- 输入保持最少:trigger / mode / confirm / host / port / model_ids(string)。
  不用 MultiCombo(多选 chips 会让节点尺寸明显变大),「指定卸载」的模型
  用逗号分隔的 STRING 填写。
- 危险操作确认:confirm 未勾选时任何模式只报告不卸载,不打断工作流。
"""

from __future__ import annotations

from comfy_api.latest import io

from ..audio_cpp_client import (
    AudioCppError,
    build_base_url,
    check_server,
    list_models,
    unload_all_models,
    unload_models,
)

# 卸载模式:
#   unload_all      —— 卸载服务器上的全部已加载模型(完全释放显存/内存)
#   unload_selected —— 只卸载下方填写 id 的模型(逗号分隔)
#   check_only      —— 不卸载,只报告当前加载状态
UNLOAD_MODES = ["unload_all", "unload_selected", "check_only"]


def server_inputs():
    """卸载节点专属的 host/port 输入(与其它节点共用默认值)。"""
    from .. import config

    return [
        io.String.Input(
            "host",
            display_name="host",
            default=config.DEFAULT_HOST,
            tooltip="audio.cpp 服务器地址(默认 127.0.0.1)。",
        ),
        io.Int.Input(
            "port",
            display_name="port",
            default=config.DEFAULT_PORT,
            min=1,
            max=65535,
            tooltip="audio.cpp 服务器端口(默认 8085)。",
        ),
    ]


class AudioCppUnloadModels(io.ComfyNode):
    """卸载 audio.cpp 模型,释放 GPU 显存与 CPU 内存。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppUnloadModels",
            display_name="AudioCpp 卸载模型(释放显存)",
            category="audio.cpp",
            description=(
                "卸载 audio.cpp 服务器上已加载的模型,彻底释放 GPU 显存与 CPU 内存。\n\n"
                "模式说明:\n"
                "- unload_all:卸载服务器上全部已加载模型(完全释放显存);\n"
                "- unload_selected:只卸载「模型 id」里填写的模型(逗号分隔);\n"
                "- check_only:不卸载,只报告当前加载状态。\n\n"
                "⚠️ 危险操作:未勾选「确认」时任何模式都只报告不卸载。\n"
                "卸载后模型需重新加载才能使用(数秒到数十秒)。"
            ),
            search_aliases=["卸载", "释放显存", "释放内存", "unload", "free vram"],
            inputs=[
                io.String.Input(
                    "trigger",
                    display_name="trigger(连接即执行)",
                    optional=True,
                    default="",
                    tooltip=(
                        "触发输入:连接任意上游节点即随图执行;"
                        "留空则作为 API 节点手动运行。"
                    ),
                ),
                io.Combo.Input(
                    "mode",
                    display_name="mode",
                    options=UNLOAD_MODES,
                    default="check_only",
                    tooltip=(
                        "卸载模式:unload_all 完全释放显存;"
                        "unload_selected 按「模型 id」填写;check_only 只查不卸。"
                    ),
                ),
                io.Boolean.Input(
                    "confirm_unload",
                    display_name="确认",
                    default=False,
                    tooltip=(
                        "⚠️ 危险操作确认。勾选后 mode 才会真正卸载;\n"
                        "未勾选时执行只报告状态,不会卸载任何模型。"
                    ),
                ),
                io.String.Input(
                    "model_ids",
                    display_name="模型 id(逗号分隔)",
                    default="qwen3_tts,qwen3_asr",
                    tooltip="当 mode=unload_selected 时,要卸载的模型 id,逗号分隔。",
                ),
                *server_inputs(),
            ],
            outputs=[
                io.String.Output(
                    "report",
                    display_name="卸载报告",
                    tooltip="本次操作的详细报告(卸载前后加载数量、卸载了哪些模型)。",
                ),
                io.String.Output(
                    "unloaded",
                    display_name="已卸载",
                    tooltip="实际被卸载的模型 id,逗号分隔。",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        trigger: str,
        mode: str,
        confirm_unload: bool,
        host: str,
        port: int,
        model_ids: str,
    ) -> io.NodeOutput:
        _ = trigger  # 触发输入:仅用于让节点随图执行
        base_url = build_base_url(host, port)
        check_server(base_url)
        models_before = list_models(base_url)
        loaded_before = [m["id"] for m in models_before if m.get("loaded")]

        # 未勾选确认时,任何模式都退化为「只查不卸」,保证节点始终可执行、
        # 不打断工作流(用户只需勾选确认 + 切换 mode 才会真正卸载)。
        if not confirm_unload:
            mode = "check_only"

        # 解析逗号分隔的模型 id,去掉空白与空项
        id_list = [i.strip() for i in model_ids.split(",") if i.strip()]

        unloaded: list[str] = []
        if mode == "check_only":
            pass
        elif mode == "unload_all":
            unloaded = unload_all_models(base_url)
        elif mode == "unload_selected":
            if not id_list:
                raise AudioCppError("unload_selected 模式需要在「模型 id」填写至少一个模型 id。")
            unloaded = unload_models(base_url, id_list)
        else:
            raise AudioCppError(f"未知的卸载模式: {mode}")

        now_loaded = [m["id"] for m in list_models(base_url) if m.get("loaded")]
        prefix = "" if confirm_unload else f"[未勾选确认,仅报告,未卸载] 请求模式: {mode} ->\n"
        report = (
            f"{prefix}模式: {mode}\n"
            f"卸载前已加载: {len(loaded_before)}\n"
            f"本次卸载: {len(unloaded)} ({', '.join(unloaded) if unloaded else '无'})\n"
            f"卸载后仍加载: {len(now_loaded)}"
        )
        return io.NodeOutput(report, ", ".join(unloaded))