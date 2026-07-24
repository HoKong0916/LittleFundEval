"""调用链 trace 日志 —— 每步记录 JSON 到 Redis List，支持 24h 回溯。

Key 结构：
    session:{id}:trace  →  List（每步一条 JSON，RPUSH 追加，EXPIRE 86400）

DEBUG_TRACE=1 时终端打印人类可读进度，原文只在 JSON 日志中保留。
Redis 不可用时自动降级为内存 list。
"""

import json
import time
import asyncio
from datetime import datetime, timezone, timedelta

import redis.asyncio as aioredis

from config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, DEBUG_TRACE

TRACE_TTL = 86400       # 24 小时
RETRY_MAX = 3
RETRY_DELAY = 1.0

# ── 事件 → 人类可读进度提示 ────────────────────────────────────
# 模板中用 {key} 占位，由 _print_progress 的 kwargs 填充。
_PROGRESS_MESSAGES: dict[str, str] = {
    # Router
    "router.classify":       "🧭 分析用户意图 → {category}",
    "router.direct_answer":  "💬 无需工具，直接回答",

    # ReAct
    "react.step.start":      "📍 步骤 {step}/{max_steps} — 正在推理下一步动作",
    "react.llm_call":        "🧠 模型思考中…（耗时 {latency_ms}ms, {tokens} tokens）",
    "react.tool_call":       "🔧 调用工具: {tool_name}",
    "react.tool_result":     "📊 工具返回数据 ({tool_name}, {result_len} 字符)",
    "react.skip_guard":      "🛡️ 拦截: 第1步试图跳过工具调用，注入纠正提示",
    "react.final_answer":    "✅ 模型给出最终回答",
    "react.forced_summary":  "⚠️ 已达最大步数，强制总结中…",
    "react.parse_error":     "❌ 模型输出无法解析，终止",

    # REWOO
    "rewoo.phase1.extract":  "🔍 阶段1: 解析基金名称…",
    "rewoo.phase1.done":     "📋 识别到 {count} 只基金: {names}",
    "rewoo.phase2.search":   "🔎 搜索基金代码: {name}",
    "rewoo.phase2.fetch":    "📡 阶段2: 并发获取 {tool_count} 项数据 ({fund_count} 只基金)…",
    "rewoo.phase2.done":     "✅ 数据获取完成，收到 {count} 条结果",
    "rewoo.phase3.start":    "🧩 阶段3: 综合分析中…",
    "rewoo.phase3.done":     "✅ 综合分析完成 ({latency_ms}ms, {tokens} tokens)",
    "rewoo.tool_error":      "⚠️ 未找到基金代码: {name}",

    # 会话
    "session.no_answer":    "⚠️ 未获得有效回复，不存入历史",
}


class TraceLogger:
    """调用链 trace 记录器。

    用法 —— CLI（async with 自动管理连接）:
        async with TraceLogger() as trace:
            await trace.log(sid, step=1, event="react.tool_call", ...)

    用法 —— FastAPI（手动生命周期）:
        trace = TraceLogger()
        await trace.connect()        # startup
        ...
        await trace.disconnect()     # shutdown
    """

    def __init__(self):
        self._redis: aioredis.Redis | None = None
        self._fallback: dict[str, list[dict]] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 Redis 连接。失败启用内存降级，不抛异常。"""
        url = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
        kwargs: dict = {"decode_responses": False}
        if REDIS_PASSWORD:
            kwargs["password"] = REDIS_PASSWORD

        for i in range(RETRY_MAX):
            try:
                self._redis = aioredis.from_url(url, **kwargs)
                await self._redis.ping()
                return
            except Exception:
                if i < RETRY_MAX - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    self._redis = None
                    print("[Trace] Redis 不可用，trace 降级为内存模式（不持久化）")

    async def disconnect(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def __aenter__(self) -> "TraceLogger":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()

    # ── 内部 ──────────────────────────────────────────────────

    @staticmethod
    def _trace_key(session_id: str) -> str:
        return f"session:{session_id}:trace"

    @property
    def _connected(self) -> bool:
        return self._redis is not None

    # ── 记录 ──────────────────────────────────────────────────

    async def log(
        self,
        session_id: str,
        *,
        step: int = 0,
        event: str,
        input: str | dict | None = None,
        output: str | dict | None = None,
        latency_ms: float = 0.0,
        tokens: dict | None = None,
    ) -> None:
        """记录一条 trace entry 到 Redis List（尾部追加），并打印进度提示。"""
        entry = {
            "session": session_id,
            "step": step,
            "event": event,
            "input": input,
            "output": output,
            "latency_ms": round(latency_ms, 1),
            "tokens": tokens,
            "ts": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }

        # 终端进度提示
        self._print_progress(event, step=step, latency_ms=round(latency_ms, 1), tokens=tokens,
                             input=input, output=output)

        # 写 Redis
        data = json.dumps(entry, ensure_ascii=False)
        if self._connected:
            key = self._trace_key(session_id)
            try:
                async with self._redis.pipeline() as pipe:
                    pipe.rpush(key, data)
                    pipe.expire(key, TRACE_TTL)
                    await pipe.execute()
            except Exception:
                self._redis = None
                self._fallback.setdefault(session_id, []).append(entry)
        else:
            self._fallback.setdefault(session_id, []).append(entry)

    # ── 回溯 ──────────────────────────────────────────────────

    async def get_trace(self, session_id: str) -> list[dict]:
        """获取完整调用链（从 Redis 或内存 fallback）。"""
        if self._connected:
            key = self._trace_key(session_id)
            try:
                raw = await self._redis.lrange(key, 0, -1)
                return [json.loads(m) for m in raw]
            except Exception:
                self._redis = None
                return self._fallback.get(session_id, [])
        return self._fallback.get(session_id, [])

    # ── 进度打印 ──────────────────────────────────────────────

    @staticmethod
    def _print_progress(event: str, **kwargs) -> None:
        """DEBUG_TRACE=1 时打印人类可读进度提示。"""
        if not DEBUG_TRACE:
            return

        template = _PROGRESS_MESSAGES.get(event)
        if template is None:
            return

        # 准备模板变量：先从 input dict 中提取，再用显式 kwargs 覆盖
        fmt: dict = {}
        inp = kwargs.get("input")
        if isinstance(inp, dict):
            for k, v in inp.items():
                if isinstance(v, (str, int, float, bool)):
                    fmt[k] = v
                elif isinstance(v, list) and len(v) <= 5:
                    fmt[k] = ", ".join(str(x) for x in v)
                elif isinstance(v, list):
                    fmt[k] = len(v)

        # output dict 中可能包含模板需要的变量
        out = kwargs.get("output")
        if isinstance(out, dict):
            for k, v in out.items():
                if isinstance(v, (str, int, float, bool)) and k not in fmt:
                    fmt[k] = v

        # 显式 kwargs 覆盖（tokens 除外，后面特殊处理）
        for k in ("step", "max_steps", "count", "tool_count", "fund_count",
                   "tool_name", "name", "names", "category", "label",
                   "latency_ms", "result_len"):
            if k in kwargs:
                fmt[k] = kwargs[k]

        # tokens 特殊处理：从 dict 中提取 total_tokens
        tokens = kwargs.get("tokens")
        if isinstance(tokens, dict):
            fmt["tokens"] = tokens.get("total_tokens", "?")

        # output 提取 result_len
        out = kwargs.get("output")
        if isinstance(out, str):
            fmt.setdefault("result_len", len(out))
        elif isinstance(out, dict):
            fmt.setdefault("result_len", len(str(out)))

        try:
            print(template.format(**fmt))
        except KeyError:
            # 模板变量缺失时退回到事件名
            print(f"[{event}]")
