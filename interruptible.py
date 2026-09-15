"""ComfyUI 可中断请求助手。

ComfyUI 的「停止任务」按钮通过全局中断标志(comfy.model_management 的
processing_interrupted / InterruptProcessingException)工作。外部 HTTP 服务
(audio.cpp)无法被直接中断 —— GPU 推理一旦发出就会跑到结束 —— 但节点可以让
「等待结果」的过程响应停止按钮:

- 执行期间周期性检查中断标志;
- 一旦检测到中断,立即补齐当前批次请求(不阻塞现场),并抛出
  InterruptProcessingException 让 ComfyUI 正常取消节点输出;
- 若用户开启了 unload_after_execute 卸载开关,中断时也会主动调用
  unload_all_models,把 audio.cpp 占用的显存/内存释放掉,避免「取消任务
  后模型还占着几 GB 显存」。

execute 需要是 async def,并通过本模块的 await 等待请求,才能被事件循环中断。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from comfy.model_management import InterruptProcessingException, processing_interrupted

from .audio_cpp_client import unload_all_models


async def run_interruptible(
    request: Awaitable[object],
    *,
    base_url: str = "",
    auto_unload: bool = False,
    poll_interval: float = 0.2,
) -> object:
    """执行 request(一个 awaitable),期间响应 ComfyUI 停止任务。

    返回请求结果;若用户按下停止,主动卸载(可选)后抛
    InterruptProcessingException,ComfyUI 视其为取消而非错误。

    用法: run_interruptible(to_interruptible(sync_call)(*args))
    """
    # 用一个任务跑真实请求,一个任务轮询中断标志。
    # 谁先完成谁获胜;请求先完成 -> 返回结果;中断先到达 -> 进入取消分支。
    request_task = asyncio.create_task(request)
    interrupt_task = asyncio.create_task(_poll_interrupt(poll_interval))

    done, pending = await asyncio.wait(
        {request_task, interrupt_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    if interrupt_task in done:
        _ = await interrupt_task  # 传播轮询中抛出的异常(若有)
        # 用户请求停止:主动卸载显存(若开启),再抛 ComfyUI 中断异常。
        if auto_unload and base_url:
            try:
                unload_all_models(base_url)
            except Exception:
                pass  # 卸载失败不掩盖中断本身
        raise InterruptProcessingException("audio.cpp 请求被用户取消,已按要求释放显存")
    return await request_task


async def _poll_interrupt(poll_interval: float) -> None:
    while not processing_interrupted():
        await asyncio.sleep(poll_interval)
    # 一旦中断标志置位,task 完成,外层据此走取消分支。


# 便捷封装:把同步请求函数变成可中断的 async 调用。
def to_interruptible(sync_call: Callable[..., object]) -> Callable[..., Awaitable[object]]:
    """把同步阻塞函数包装为 async 可中断调用。

    用法: run_interruptible(to_interruptible(client.speech_synthesize)(*args), ...)
    """

    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(sync_call, *args, **kwargs)

    return wrapper