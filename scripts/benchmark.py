"""Benchmark —— 收集简历所需的 5 项实测数据

用法:
    python scripts/benchmark.py                      # 全量（需 DeepSeek + llama.cpp + Redis）
    python scripts/benchmark.py --rewoo-concurrency  # 仅 REWOO 并发加速比（无需外部服务）

5 项指标:
    1. 双 LLM 降本比例   : 双 LLM vs 纯云端的 token 消耗对比
    2. 本地承接占比      : local_chat 调用次数 / 总 LLM 调用次数
    3. REWOO 并发加速比  : N 只基金串行 vs asyncio.gather 墙钟对比（模拟，无需外部服务）
    4. 增量解析收益      : ReAct 增量截断 vs 等完整 buffer 的 token 差
    5. 单请求 token 区间 : DirectAnswer / ReAct / REWOO 各执行器 token 中位数
"""

import argparse
import asyncio
import os
import random
import sys
import time
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 测试问题集（覆盖三类执行器）──────────────────────────────────
QUESTIONS = [
    # DirectAnswer：纯知识问答，1 次 cloud_chat
    {"q": "什么是最大回撤？", "category": "DirectAnswer"},
    {"q": "定投和一次性买入哪个好？", "category": "DirectAnswer"},
    # ReAct：单基金链式推理，最多 5 步 cloud_chat
    {"q": "519702 最近表现怎么样？", "category": "ReAct"},
    {"q": "005827 适合加仓吗？", "category": "ReAct"},
    # REWOO：多基金批量对比，1 次 cloud_chat + N 次 local_chat
    {"q": "全面分析一下 519702", "category": "REWOO"},
    {"q": "对比 005827 和 161725 的表现和持仓", "category": "REWOO"},
    {"q": "对比 519702、005827 和 161725 的持仓", "category": "REWOO"},
]


# ════════════════════════════════════════════════════════════════
# #3 REWOO 并发加速比（模拟，无需外部服务）
# ════════════════════════════════════════════════════════════════

# 模拟工具调用的典型耗时（基于实际 HTTP 请求分布）
_TOOL_LATENCY = {
    "get_fund_performance": (0.8, 1.3),   # 蛋卷+fundgz+akshare 三路并发
    "get_fund_holdings":    (0.6, 1.1),   # HTML 解析 + 批量行情
    "estimate_fund_nav":    (1.0, 1.5),   # 依赖 holdings + 板块涨幅
    "search_fund":          (0.3, 0.6),   # 单次 JSONP
}


async def _mock_tool(tool_name: str, fund_code: str) -> str:
    lo, hi = _TOOL_LATENCY.get(tool_name, (0.5, 1.0))
    await asyncio.sleep(random.uniform(lo, hi))
    return f"{tool_name}:{fund_code}"


async def _serial_fetch(funds: list[str], tools: list[str]) -> float:
    t0 = time.perf_counter()
    for fund in funds:
        for tool in tools:
            await _mock_tool(tool, fund)
    return time.perf_counter() - t0


async def _concurrent_fetch(funds: list[str], tools: list[str]) -> float:
    t0 = time.perf_counter()
    tasks = [_mock_tool(tool, fund) for fund in funds for tool in tools]
    await asyncio.gather(*tasks)
    return time.perf_counter() - t0


async def bench_rewoo_concurrency() -> None:
    """#3 REWOO 并发加速比：模拟 N 只基金 × M 个工具的串行 vs gather。"""
    print("\n" + "=" * 70)
    print("#3 REWOO 并发加速比（模拟值，基于工具典型网络耗时分布）")
    print("=" * 70)

    scenarios = [
        ("1 基金 × 2 工具", ["519702"], ["get_fund_performance", "get_fund_holdings"]),
        ("2 基金 × 2 工具", ["519702", "005827"], ["get_fund_performance", "get_fund_holdings"]),
        ("3 基金 × 2 工具", ["519702", "005827", "161725"], ["get_fund_performance", "get_fund_holdings"]),
        ("3 基金 × 3 工具", ["519702", "005827", "161725"],
         ["get_fund_performance", "get_fund_holdings", "estimate_fund_nav"]),
    ]

    ROUNDS = 20

    for label, funds, tools in scenarios:
        serial_times, concurrent_times = [], []
        for _ in range(ROUNDS):
            serial_times.append(await _serial_fetch(funds, tools))
            concurrent_times.append(await _concurrent_fetch(funds, tools))

        serial_times.sort()
        concurrent_times.sort()
        s_med = serial_times[ROUNDS // 2]
        c_med = concurrent_times[ROUNDS // 2]
        speedup = s_med / c_med if c_med > 0 else 0

        n_calls = len(funds) * len(tools)
        print(f"\n  {label}（{n_calls} 次工具调用）:")
        print(f"    串行中位数:   {s_med:.2f}s")
        print(f"    并发中位数:   {c_med:.2f}s")
        print(f"    加速比:       {speedup:.1f}x")

    print("\n  说明：基于工具典型 HTTP 耗时模拟，实测加速比受网络波动影响。")
    print("  理论上限 = 工具调用次数 N×M（所有工具耗时相同时）。")


# ════════════════════════════════════════════════════════════════
# #1 #2 #4 #5：需 DeepSeek + llama.cpp + Redis
# ════════════════════════════════════════════════════════════════

async def bench_cost_and_token() -> None:
    """#1 双 LLM 降本比例 + #2 本地承接占比 + #5 单请求 token 区间。"""
    from core.chat import run_chat
    from core.memory import MemoryManager
    from core.trace import TraceLogger
    import llm_client

    print("\n" + "=" * 70)
    print("#1 双 LLM 降本比例  |  #2 本地承接占比  |  #5 单请求 token 区间")
    print("=" * 70)
    print("  需 DeepSeek + llama.cpp + Redis 可用，请稍候…\n")

    memory = MemoryManager()
    trace = TraceLogger()
    await memory.connect()
    await trace.connect()

    # ── 统计容器 ──
    cloud_tokens: list[int] = []       # 每次 cloud_chat 的 total_tokens
    local_calls = 0                     # local_chat 调用次数
    cloud_calls = 0                     # cloud_chat 调用次数
    per_category_tokens: dict[str, list[int]] = {}  # 按执行器分组的 token

    # 拦截 _deduct_budget 统计 cloud_chat token
    original_deduct = llm_client._deduct_budget

    async def patched_deduct(session_id, total_tokens):
        nonlocal cloud_calls
        cloud_calls += 1
        cloud_tokens.append(total_tokens)
        await original_deduct(session_id, total_tokens)

    # 拦截 local_chat 统计调用次数
    original_local = llm_client.local_chat

    async def patched_local(messages, temperature=0.0):
        nonlocal local_calls
        local_calls += 1
        return await original_local(messages, temperature)

    # ── 第一阶段：双 LLM 模式 ──
    print("  [1/2] 双 LLM 模式（本地路由 + 云端推理）…")
    dual_cloud_tokens: list[int] = []
    dual_local = 0
    dual_cloud = 0

    with patch("llm_client._deduct_budget", patched_deduct), \
         patch("llm_client.local_chat", patched_local):
        for item in QUESTIONS:
            sid = f"bench_dual_{item['category']}"
            cloud_tokens.clear()
            local_calls = 0
            cloud_calls = 0

            try:
                result = await asyncio.wait_for(
                    run_chat(sid, item["q"], memory, trace),
                    timeout=120,
                )
                cat = result["category"]
                call_total = sum(cloud_tokens)
                per_category_tokens.setdefault(cat, []).append(call_total)
                dual_cloud_tokens.extend(cloud_tokens)
                dual_local += local_calls
                dual_cloud += cloud_calls
                print(f"    ✓ {item['q'][:30]:<30} → {cat:<14} "
                      f"cloud={cloud_calls}次 local={local_calls}次 "
                      f"token={call_total}")
            except Exception as e:
                print(f"    ✗ {item['q'][:30]:<30} → {type(e).__name__}: {e}")

    # ── 第二阶段：纯云端模式（local_chat 全部走 DeepSeek）──
    print("\n  [2/2] 纯云端模式（所有 LLM 调用走 DeepSeek）…")

    async def cloud_as_local(messages, temperature=0.0):
        """local_chat 的纯云端替代：非流式 DeepSeek 调用。"""
        nonlocal cloud_calls
        client = llm_client._get_deepseek_client()
        resp = await client.chat.completions.create(
            model=llm_client.DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            timeout=30,
        )
        cloud_calls += 1
        cloud_tokens.append(resp.usage.total_tokens)
        return resp.choices[0].message.content

    cloud_only_tokens: list[int] = []
    cloud_only_calls = 0

    with patch("llm_client.local_chat", cloud_as_local), \
         patch("llm_client._deduct_budget", patched_deduct):
        for item in QUESTIONS:
            sid = f"bench_cloud_{item['category']}"
            cloud_tokens.clear()
            cloud_calls = 0

            try:
                result = await asyncio.wait_for(
                    run_chat(sid, item["q"], memory, trace),
                    timeout=120,
                )
                call_total = sum(cloud_tokens)
                cloud_only_tokens.extend(cloud_tokens)
                cloud_only_calls += cloud_calls
                print(f"    ✓ {item['q'][:30]:<30} → token={call_total}")
            except Exception as e:
                print(f"    ✗ {item['q'][:30]:<30} → {type(e).__name__}: {e}")

    # ── 汇总 ──
    print("\n" + "-" * 70)
    print("  ## #1 双 LLM 降本比例")
    if dual_cloud_tokens and cloud_only_tokens:
        dual_total = sum(dual_cloud_tokens)
        cloud_total = sum(cloud_only_tokens)
        saved = (1 - dual_total / cloud_total) * 100 if cloud_total > 0 else 0
        print(f"    双 LLM 总 token:   {dual_total:,}")
        print(f"    纯云端总 token:    {cloud_total:,}")
        print(f"    降本比例:          {saved:.1f}%")
    else:
        print("    数据不足，请检查 LLM 是否可用")

    print("\n  ## #2 本地承接占比")
    total_llm_calls = dual_local + dual_cloud
    if total_llm_calls > 0:
        pct = dual_local / total_llm_calls * 100
        print(f"    local_chat 调用:   {dual_local} 次")
        print(f"    cloud_chat 调用:   {dual_cloud} 次")
        print(f"    总 LLM 调用:       {total_llm_calls} 次")
        print(f"    本地承接占比:      {pct:.1f}%")
    else:
        print("    数据不足")

    print("\n  ## #5 单请求 token 区间（按执行器）")
    for cat, tokens in sorted(per_category_tokens.items()):
        if tokens:
            tokens.sort()
            med = tokens[len(tokens) // 2]
            print(f"    {cat:<14} 中位数={med:,}  最小={min(tokens):,}  最大={max(tokens):,}  样本={len(tokens)}")

    await memory.disconnect()
    await trace.disconnect()


# ════════════════════════════════════════════════════════════════
# #4 增量解析收益
# ════════════════════════════════════════════════════════════════

async def bench_incremental_parsing() -> None:
    """#4 增量解析收益：同一 ReAct 问题，截断 vs 等完整的 token 差。

    方法：跑两次同一问题——
      1. 正常模式（增量截断）：_try_parse_action 检测到 Action 即返回
      2. 禁用截断：_try_parse_action 始终返回 None，强制走完整 buffer
    对比两次 cloud_chat 的 total_tokens。
    """
    from core.chat import run_chat
    from core.memory import MemoryManager
    from core.trace import TraceLogger
    import llm_client

    print("\n" + "=" * 70)
    print("#4 增量解析收益（ReAct 截断 vs 等完整）")
    print("=" * 70)
    print("  需 DeepSeek + llama.cpp + Redis 可用，请稍候…\n")

    memory = MemoryManager()
    trace = TraceLogger()
    await memory.connect()
    await trace.connect()

    react_questions = [q for q in QUESTIONS if q["category"] == "ReAct"]

    cloud_tokens: list[int] = []
    original_deduct = llm_client._deduct_budget

    async def patched_deduct(session_id, total_tokens):
        cloud_tokens.append(total_tokens)
        await original_deduct(session_id, total_tokens)

    # ── 正常模式（增量截断）──
    print("  [1/2] 正常模式（增量截断）…")
    trunc_tokens: list[int] = []
    with patch("llm_client._deduct_budget", patched_deduct):
        for item in react_questions:
            sid = f"bench_trunc_{hash(item['q']) % 10000}"
            cloud_tokens.clear()
            try:
                await asyncio.wait_for(
                    run_chat(sid, item["q"], memory, trace),
                    timeout=120,
                )
                trunc_tokens.extend(cloud_tokens)
                print(f"    ✓ {item['q'][:30]:<30} → {len(cloud_tokens)} 步, token={sum(cloud_tokens)}")
            except Exception as e:
                print(f"    ✗ {item['q'][:30]:<30} → {type(e).__name__}: {e}")

    # ── 禁用截断（等完整 buffer）──
    print("\n  [2/2] 禁用截断（等完整 buffer）…")
    full_tokens: list[int] = []
    with patch("llm_client._deduct_budget", patched_deduct), \
         patch("core.react_loop._try_parse_action", return_value=None):
        for item in react_questions:
            sid = f"bench_full_{hash(item['q']) % 10000}"
            cloud_tokens.clear()
            try:
                await asyncio.wait_for(
                    run_chat(sid, item["q"], memory, trace),
                    timeout=120,
                )
                full_tokens.extend(cloud_tokens)
                print(f"    ✓ {item['q'][:30]:<30} → {len(cloud_tokens)} 步, token={sum(cloud_tokens)}")
            except Exception as e:
                print(f"    ✗ {item['q'][:30]:<30} → {type(e).__name__}: {e}")

    # ── 汇总 ──
    print("\n" + "-" * 70)
    if trunc_tokens and full_tokens:
        trunc_total = sum(trunc_tokens)
        full_total = sum(full_tokens)
        saved = full_total - trunc_total
        pct = (saved / full_total * 100) if full_total > 0 else 0
        print(f"    增量截断总 token:  {trunc_total:,}")
        print(f"    等完整总 token:    {full_total:,}")
        print(f"    节省 token:        {saved:,} ({pct:.1f}%)")
    else:
        print("    数据不足，请检查 LLM 是否可用")

    await memory.disconnect()
    await trace.disconnect()


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="简历实测数据收集")
    parser.add_argument(
        "--rewoo-concurrency", action="store_true",
        help="仅跑 #3 REWOO 并发加速比（无需外部服务）",
    )
    args = parser.parse_args()

    if args.rewoo_concurrency:
        await bench_rewoo_concurrency()
        return

    # 全量测试
    await bench_rewoo_concurrency()
    await bench_cost_and_token()
    await bench_incremental_parsing()

    print("\n" + "=" * 70)
    print("全部完成。将以上数字填入简历 {N} 占位符。")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
