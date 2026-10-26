"""AudioCppSyncModels: 一键把本地 model_specs 里的模型家族同步注册到服务器。

audio.cpp 的模型注册表(server.json)与模型文件是解耦的:通过 WebUI 下载或
手动放入 models/ 目录的新模型不会自动出现在 /v1/models,首次请求即 500
"unknown model id"。本节点遍历本地 model_specs/*.json 声明的全部家族,
逐个调 ensure_model_registered(已注册的自动跳过,未注册的经
POST /v1/models/load 运行时补注册),并输出报告。

需要服务器以 --ui-management 启动(本机启动脚本已包含该参数)。

注意:注册 = 立即加载占显存(服务器 load 是同步的)。已注册的模型不做
任何变更;要释放显存请用「AudioCpp 卸载模型(释放显存)」节点。
"""

from __future__ import annotations

from comfy_api.latest import io

from ..audio_cpp_client import AudioCppError, build_base_url, check_server, ensure_model_registered
from ..model_profiles import spec_families


class AudioCppSyncModels(io.ComfyNode):
    """把本地 model_specs 声明的模型家族同步注册到 audio.cpp 服务器。"""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AudioCppSyncModels",
            display_name="AudioCpp 模型同步(注册新模型)",
            category="audio.cpp",
            description=(
                "一键把本地 model_specs/*.json 声明的模型家族同步注册到 audio.cpp 服务器。\n\n"
                "适用场景:通过 WebUI 下载或手动放入 models/ 目录的新模型,\n"
                "服务器注册表里还没有它,直接请求会报 \"unknown model id\"。\n"
                "本节点遍历全部 spec 家族:已注册的跳过,未注册的经\n"
                "POST /v1/models/load 运行时补注册(默认路径 models/<family>)。\n\n"
                "⚠️ 注册 = 立即加载占显存(服务器 load 是同步的)。\n"
                "需要服务器以 --ui-management 启动(本机启动脚本已包含)。"
            ),
            search_aliases=["同步", "注册", "sync", "register model", "新模型"],
            inputs=[
                io.String.Input(
                    "host",
                    display_name="服务器地址",
                    default="127.0.0.1",
                    tooltip="audio.cpp 服务器地址(默认 127.0.0.1)。",
                ),
                io.Int.Input(
                    "port",
                    display_name="端口",
                    default=8085,
                    min=1,
                    max=65535,
                    tooltip="audio.cpp 服务器端口(默认 8085)。",
                ),
                io.Float.Input(
                    "timeout_seconds",
                    display_name="单个模型超时(秒)",
                    default=600.0,
                    min=10.0,
                    max=3600.0,
                    display_mode=io.NumberDisplay.slider,
                    tooltip="单个模型注册(含加载)的超时时间。大模型冷加载需要较长时间。",
                ),
            ],
            outputs=[
                io.String.Output(
                    "report",
                    display_name="同步报告",
                    tooltip="同步结果:新注册 / 已存在跳过 / 失败(附原因)三个清单。",
                ),
            ],
        )

    @classmethod
    async def execute(
        cls,
        host: str,
        port: int,
        timeout_seconds: float,
    ) -> io.NodeOutput:
        base_url = build_base_url(host, port)
        check_server(base_url)
        from ..interruptible import run_interruptible, to_interruptible

        families = sorted(spec_families().keys())
        if not families:
            raise AudioCppError("本地 model_specs/ 目录没有任何模型 spec,无法同步。")

        newly: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        for family in families:
            try:
                from ..audio_cpp_client import list_models

                known = {str(e.get("id")) for e in list_models(base_url)}
                if family in known:
                    skipped.append(family)
                    continue
                model_id = await run_interruptible(
                    to_interruptible(ensure_model_registered)(
                        base_url, family, timeout=float(timeout_seconds)
                    ),
                    base_url=base_url,
                )
                newly.append(model_id)
            except AudioCppError as exc:
                failed.append(f"{family}: {exc}")

        lines = [f"共扫描 {len(families)} 个 spec 家族:"]
        lines.append(f"  新注册 {len(newly)}: {', '.join(newly) if newly else '无'}")
        lines.append(f"  已存在跳过 {len(skipped)}: {', '.join(skipped) if skipped else '无'}")
        if failed:
            lines.append(f"  失败 {len(failed)}:")
            lines.extend(f"    - {line}" for line in failed)
        else:
            lines.append("  失败 0")
        return io.NodeOutput("\n".join(lines))
