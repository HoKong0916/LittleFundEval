"""飞书消息发送 —— 回复消息到飞书用户（占位消息 + 最终回复 + 错误提示）。

所有函数内部均捕获异常，飞书 API 调用失败时打日志但不抛异常。
确保消息发送失败不会导致 robot 进程崩溃。

【关键】lark_oapi 的导入必须延迟到函数内部！
    lark_oapi/__init__.py 顶层有 `from . import ws`，会在首次 import 时
    执行 lark_oapi/ws/client.py 的模块级 `loop = asyncio.get_event_loop()`，
    将 event loop 绑定到导入时所在线程。如果在主线程（uvicorn）顶层导入，
    loop 会被绑定到主线程，导致 daemon 线程中 WebSocket 连接失败
    （"coroutine 'Client._connect' was never awaited"）。
"""

import json
import logging

from config import FEISHU_APP_ID, FEISHU_APP_SECRET

logger = logging.getLogger(__name__)

# 飞书单条消息上限约 10KB，留 1KB 缓冲
_MAX_CONTENT_LENGTH = 9000


def _build_client():
    """构建飞书 SDK Client 实例（HTTP 客户端，非 WebSocket）。"""
    from lark_oapi import Client  # 延迟导入，避免主线程绑定 event loop
    return Client.builder() \
        .app_id(FEISHU_APP_ID) \
        .app_secret(FEISHU_APP_SECRET) \
        .build()


async def send_reply(user_id: str, content: str, msg_id: str) -> None:
    """回复消息到飞书用户。

    Args:
        user_id: 接收者的 open_id
        content: 回复文本内容，超过 9000 字符时自动截断并追加提示
        msg_id: 用户原消息的 message_id，用于关联到对话线程下
    """
    from lark_oapi.api.im.v1 import (  # 延迟导入，避免主线程绑定 event loop
        ReplyMessageRequestBody,
        ReplyMessageRequest,
        ReplyMessageResponse,
    )

    if len(content) > _MAX_CONTENT_LENGTH:
        content = content[:_MAX_CONTENT_LENGTH] + "\n…(内容过长已截断)"

    client = _build_client()
    # 飞书 msg_type=text 时，content 必须是 JSON 字符串: {"text": "实际内容"}
    body = ReplyMessageRequestBody()
    body.content = json.dumps({"text": content}, ensure_ascii=False)
    body.msg_type = "text"
    request = (
        ReplyMessageRequest.builder()
        .message_id(msg_id)
        .request_body(body)
        .build()
    )

    try:
        # areply 是 async 版本（reply 是同步版本，不能 await）
        response: ReplyMessageResponse = await client.im.v1.message.areply(request)
        if not response.success():
            logger.error(
                "飞书回复消息失败: code=%s msg=%s log_id=%s",
                response.code, response.msg, response.get_log_id(),
            )
    except Exception:
        logger.exception("飞书回复消息异常，跳过不崩溃")


async def send_error(user_id: str, msg_id: str, reason: str) -> None:
    """发送错误提示（限流/超时/异常等场景）。

    reason 会作为消息正文发送给用户。
    """
    await send_reply(user_id, reason, msg_id)
