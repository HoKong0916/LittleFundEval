"""LLM 客户端封装 —— 本地 llama.cpp（local_chat）与云端 DeepSeek（cloud_chat 流式）。

local_chat 在 llama-server 不可用时支持降级到 DeepSeek（受 LLM_FALLBACK_TO_CLOUD 控制）。
cloud_chat 按用户（session_id）执行每日 token 预算管控，超额拒绝调用。
"""

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta

import redis.asyncio as aioredis
from openai import AsyncOpenAI

from config import (
    DAILY_TOKEN_BUDGET,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLAMA_CPP_BASE_URL,
    LLM_FALLBACK_TO_CLOUD,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(base_url=LLAMA_CPP_BASE_URL, api_key="not-needed")
_deepseek_client: AsyncOpenAI | None = None
_model_name: str | None = None


def _get_deepseek_client() -> AsyncOpenAI:
    """惰性构建 DeepSeek 客户端。

    缺失 API key 时不在此处崩溃，而是推迟到首次实际调用时抛出——
    便于无 key 环境下导入本模块（如单元测试、纯本地 LLM 模式）。
    """
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = AsyncOpenAI(base_url=DEEPSEEK_BASE_URL, api_key=DEEPSEEK_API_KEY)
    return _deepseek_client

# ── token 预算：每次调用 cloud_chat 时现建 Redis 连接，用完即关 ──
# 开销 < 1ms（localhost），相比 LLM 调用可忽略；天然线程安全，无需缓存


def _build_redis_url() -> str:
    return f"redis://{REDIS_HOST}:{REDIS_PORT}/0"


def _budget_key(session_id: str) -> str:
    """按自然日生成 key：lg:budget:{session_id}:{YYYYMMDD}。"""
    return f"lg:budget:{session_id}:{datetime.now().strftime('%Y%m%d')}"


def _budget_ttl() -> int:
    """预算 key TTL：到当天 23:59:59 的剩余秒数。

    避免 ex=固定值 + nx=True 组合导致的"当天额度翻倍" bug：
    早 8 点用 ex=43200(12h)，晚 8 点 key 过期后下次扣减会 nx=True 重新初始化满额，
    等于当天下午+晚上额度翻倍。改为到当天结束过期，跨天自然换新 key。
    """
    midnight = datetime.combine(date.today() + timedelta(days=1), time.min)
    return max(1, int((midnight - datetime.now()).total_seconds()))


# 单次调用最小预留额度：ReAct/REWOO 单步 prompt 约 3-5k + 输出 2k，预留 2000 作为放行下限。
# 低于此值直接拒绝，避免"剩余 1 token 照发 5 万"——check 是软限，只看 >0 拦不住单次大额调用。
MIN_SINGLE_CALL_TOKENS = 2000


async def _check_budget(session_id: str) -> bool:
    """检查当日剩余额度是否够一次最小调用。key 不存在视为满额。Redis 不可用降级放行。"""
    if not session_id:
        return True
    try:
        redis = aioredis.from_url(
            _build_redis_url(), decode_responses=True,
            password=REDIS_PASSWORD or None,
        )
        try:
            raw = await redis.get(_budget_key(session_id))
            remaining = int(raw) if raw is not None else DAILY_TOKEN_BUDGET
            return remaining > MIN_SINGLE_CALL_TOKENS
        finally:
            await redis.aclose()
    except Exception:
        return True


async def _deduct_budget(session_id: str, total_tokens: int) -> None:
    """扣减当日剩余额度（DECRBY），首次访问自动初始化为全额。"""
    if not session_id or total_tokens <= 0:
        return
    try:
        redis = aioredis.from_url(
            _build_redis_url(), decode_responses=True,
            password=REDIS_PASSWORD or None,
        )
        try:
            key = _budget_key(session_id)
            await redis.set(key, DAILY_TOKEN_BUDGET, nx=True, ex=_budget_ttl())
            await redis.decrby(key, total_tokens)
        finally:
            await redis.aclose()
    except Exception:
        logger.warning("token 预算扣减失败")


async def _get_model_name() -> str:
    """惰性获取并缓存 llama-server 首个模型 ID，避免每次请求重复拉取模型列表。"""
    global _model_name
    if _model_name == None:
        models = await _client.models.list()
        _model_name = models.data[0].id
    return _model_name


# 降级熔断：local_chat 降级到云端是兜底，但降级态下路由/摘要/REWOO提取全在无感烧 DeepSeek。
# 连续降级超阈值则强制关闭降级（抛错逼运维介入），避免 llama-server 挂了静默烧钱。
_FALLBACK_FAILURE_COUNT = 0
_FALLBACK_TRIP_THRESHOLD = 3


async def local_chat(messages: list[dict], temperature: float = 0.0) -> str:
    """本地 llama.cpp — 轻量分类。

    llama-server 需提前手动启动（与 Redis 同理，应用不负责进程管理）。
    连不上时若 LLM_FALLBACK_TO_CLOUD=1 则自动降级到 DeepSeek，但连续降级超阈值
    会熔断（抛错），避免 llama-server 长时间挂掉时降级路径静默烧光云端预算。
    """
    global _FALLBACK_FAILURE_COUNT
    try:
        response = await _client.chat.completions.create(
            model=await _get_model_name(),
            messages=messages,
            temperature=temperature,
        )
        _FALLBACK_FAILURE_COUNT = 0  # 成功一次即清零
        return response.choices[0].message.content
    except Exception:
        if not LLM_FALLBACK_TO_CLOUD:
            raise
        _FALLBACK_FAILURE_COUNT += 1
        if _FALLBACK_FAILURE_COUNT > _FALLBACK_TRIP_THRESHOLD:
            logger.error(
                "llama-server 连续降级 %d 次，已熔断降级路径，请检查 llama-server 状态",
                _FALLBACK_FAILURE_COUNT,
            )
            raise
        logger.warning(
            "llama-server 不可用（第 %d 次），降级到 DeepSeek",
            _FALLBACK_FAILURE_COUNT,
        )
        # 降级调用加 timeout，避免 DeepSeek 卡住导致路由分类/摘要/REWOO提取挂死
        response = await _get_deepseek_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            timeout=30,
        )
        return response.choices[0].message.content


async def cloud_chat(
    messages: list[dict],
    temperature: float = 0.0,
    tools: list[dict] | None = None,
    session_id: str = "",
):
    """云端 DeepSeek — 流式评估，支持 function calling。

    yield:
      {"type": "text", "content": "增量文本"}
      {"type": "tool_calls", "calls": [{"id": "...", "name": "...", "arguments": {...}}]}
      {"type": "done", "finish_reason": "stop" | "tool_calls" | "cancelled" | "error" | "budget_exceeded"}
    """
    # ── 预算预检：当日剩余额度 ≤ 0 → 直接拒绝，省 API 费 ──
    if session_id and not await _check_budget(session_id):
        yield {"type": "text", "content": "今日对话额度已用尽，明天再试吧~"}
        yield {"type": "done", "finish_reason": "budget_exceeded"}
        return

    kwargs: dict = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    stream = None
    tool_buf: dict[int, dict] = {}
    usage: dict | None = None
    try:
        # create() 本身可能抛异常（网络断/认证失败/503），放在 try 内赋值，
        # 否则 finally 里 stream 未定义，aclose() 会抛 NameError 掩盖原始异常
        stream = await _get_deepseek_client().chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta

            if delta.content:
                yield {"type": "text", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    tool_buf.setdefault(tc.index, {"id": tc.id or "", "name": "", "args": ""})
                    if tc.id:
                        tool_buf[tc.index]["id"] = tc.id
                    if tc.function and tc.function.name:
                        tool_buf[tc.index]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_buf[tc.index]["args"] += tc.function.arguments

            # DeepSeek 在 streaming 模式下，usage 信息出现在最后一个 chunk
            # 将其附加到 done 事件中，供上层 trace 记录 token 消耗
            if chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

        # ── 预算实扣：用 DeepSeek 返回的权威 total_tokens ──
        if usage and session_id:
            await _deduct_budget(session_id, usage["total_tokens"])

        # 构造 done 事件，统一携带 finish_reason + usage
        done = {"type": "done"}
        if usage:
            done["usage"] = usage

        if tool_buf:
            calls = [tool_buf[i] for i in sorted(tool_buf)]
            yield {
                "type": "tool_calls",
                "calls": [
                    {"id": b["id"], "name": b["name"], "arguments": json.loads(b["args"])}
                    for b in calls
                ],
            }
            done["finish_reason"] = "tool_calls"
        else:
            done["finish_reason"] = "stop"

        yield done

    except asyncio.CancelledError:
        yield {"type": "done", "finish_reason": "cancelled"}
    except GeneratorExit:
        # async generator 被外部 aclose() 正常终止 —— 直接返回，不再 yield
        return
    except Exception:
        yield {"type": "done", "finish_reason": "error"}
        raise
    finally:
        # stream 可能因 create() 抛异常而未赋值，需判空
        if stream is not None:
            await stream.response.aclose()
