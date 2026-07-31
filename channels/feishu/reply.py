"""飞书消息发送 —— 回复消息到原消息所在对话。

异常全部捕获打日志，发送失败不抛异常避免进程崩溃。

lark_oapi 必须延迟导入：SDK 顶层会执行 `loop = asyncio.get_event_loop()`，
主线程先 import 会绑死 loop，daemon 线程 WebSocket 连接就报
"coroutine 'Client._connect' was never awaited"。详见 bot.py 模块 docstring。
"""

import json
import logging

from config import FEISHU_APP_ID, FEISHU_APP_SECRET

logger = logging.getLogger(__name__)

# 飞书单条消息上限约 10KB，留 1KB 缓冲
_MAX_CONTENT_LENGTH = 9000


def _build_client():
    """构建飞书 SDK Client 实例（HTTP 客户端，非 WebSocket）。"""
    import lark_oapi as lark  # 延迟导入，避免主线程绑定 event loop
    return (
        lark.Client.builder()
        .app_id(FEISHU_APP_ID)
        .app_secret(FEISHU_APP_SECRET)
        .build()
    )


async def send_reply(content: str, msg_id: str) -> None:
    """回复消息到指定 msg_id 的对话。超 9000 字符截断。"""
    import lark_oapi as lark  # 延迟导入，避免主线程绑定 event loop

    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH] + "\n…(内容过长已截断)"

    client = _build_client()
    request = (
        lark.im.v1.ReplyMessageRequest.builder()
        .message_id(msg_id)
        .request_body(
            lark.im.v1.ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": content}, ensure_ascii=False))
            .msg_type("text")
            .build()
        )
        .build()
    )

    try:
        response = await client.im.v1.message.areply(request)
        if not response.success():
            logger.error(
                "飞书回复消息失败: code=%s msg=%s log_id=%s",
                response.code, response.msg, response.get_log_id(),
            )
    except Exception:
        logger.exception("飞书回复消息异常，跳过不崩溃")


