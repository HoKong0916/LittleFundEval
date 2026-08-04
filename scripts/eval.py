"""Router 意图分类评估脚本 —— 在固定标注问题集上量化分类与工具选择质量。

用法（从项目根目录执行）:
    python scripts/eval.py

需要本地 llama.cpp 可用，或设置 DEEPSEEK_API_KEY 走云端降级。
输出: 每条问题的 (期望/预测/类别命中/工具精确率/召回率) 表格 + 汇总指标。

指标说明:
    - category_accuracy : 意图类别 (DirectAnswer/ReAct/REWOO) 命中率
    - tool_exact_match  : tools_needed 集合与标注完全一致的比例
    - tool_precision    : 预测工具中属于标注的比例（越低代表多调了无关工具）
    - tool_recall       : 标注工具被预测覆盖的比例（越低代表漏调了必要工具）
"""

import asyncio
import os
import sys

# 将项目根目录加入 sys.path，使 core 包可被导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.router import classify_intent  # noqa: E402
from core.trace import TraceLogger  # noqa: E402


# ── 标注数据集（取自 router Few-Shot，覆盖三类意图与典型工具组合）─────────
DATASET: list[dict] = [
    {"q": "什么是最大回撤？", "category": "DirectAnswer", "tools": []},
    {"q": "定投和一次性买入哪个好？", "category": "DirectAnswer", "tools": []},
    {"q": "519702 去年收益多少？", "category": "ReAct", "tools": ["get_fund_performance"]},
    {"q": "全面分析一下 519702，各方面都想了解一下。",
     "category": "REWOO", "tools": ["get_fund_performance", "get_fund_holdings"]},
    {"q": "对比一下 005827 和 260108 的收益、风险和持仓。",
     "category": "REWOO", "tools": ["get_fund_performance", "get_fund_holdings"]},
    {"q": "人工智能板块资金流向怎么样？",
     "category": "ReAct", "tools": ["capital_inflow_in_sectors"]},
    {"q": "消费电子最近表现如何？", "category": "ReAct", "tools": ["select_fund"]},
    {"q": "大摩数字经济混合C今日适合加仓吗？",
     "category": "ReAct", "tools": ["search_fund"]},
    {"q": "新能源板块有哪些表现不错的基金？",
     "category": "ReAct", "tools": ["select_fund"]},
    {"q": "对比一下易方达蓝筹精选和景顺长城新兴成长的收益、风险和持仓。",
     "category": "REWOO",
     "tools": ["search_fund", "get_fund_performance", "get_fund_holdings"]},
]


def _prf(expected: set[str], predicted: set[str]) -> tuple[float, float]:
    """返回 (precision, recall)。空集按约定处理。"""
    if not predicted and not expected:
        return 1.0, 1.0
    if not predicted:
        return 0.0, 0.0 if expected else 1.0
    tp = len(expected & predicted)
    precision = tp / len(predicted)
    recall = 1.0 if not expected else tp / len(expected)
    return precision, recall


def _truncate(s: str, n: int = 26) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


async def main() -> int:
    trace = TraceLogger()  # 未连接 Redis → 内存 fallback，评估不落 trace
    rows: list[dict] = []

    print("=" * 92)
    print(f"{'问题':<28} {'期望':<14} {'预测':<14} {'类别':<5} {'工具P':<6} {'工具R':<6}")
    print("-" * 92)

    for item in DATASET:
        q = item["q"]
        exp_cat = item["category"]
        exp_tools = set(item["tools"])
        try:
            decision = await classify_intent(
                [{"role": "user", "content": q}],
                history=None,
                trace=trace,
                session_id="eval",
            )
            pred_cat = decision["category"]
            pred_tools = set(decision.get("tools_needed", []))
        except Exception as e:  # LLM 不可用等
            pred_cat = "ERROR"
            pred_tools = set()
            rows.append({"q": q, "exp": exp_cat, "pred": pred_cat,
                         "cat_ok": False, "exact": False, "p": 0.0, "r": 0.0, "err": str(e)})
            print(f"{_truncate(q):<28} {exp_cat:<14} {pred_cat:<14} {'✗':<5} {'-':<6} {'-':<6}  ← {type(e).__name__}")
            continue

        p, r = _prf(exp_tools, pred_tools)
        cat_ok = pred_cat == exp_cat
        exact = pred_tools == exp_tools
        rows.append({"q": q, "exp": exp_cat, "pred": pred_cat,
                     "cat_ok": cat_ok, "exact": exact, "p": p, "r": r})

        print(f"{_truncate(q):<28} {exp_cat:<14} {pred_cat:<14} "
              f"{'✓' if cat_ok else '✗':<5} {p:<6.2f} {r:<6.2f}")

    # ── 汇总 ──
    total = len(rows)
    if total == 0:
        print("\n无评估数据。")
        return 1

    errors = [x for x in rows if x["pred"] == "ERROR"]
    if errors:
        print(f"\n⚠️ {len(errors)}/{total} 条调用失败（LLM 不可用？）: {errors[0]['err']}")

    valid = [x for x in rows if x["pred"] != "ERROR"]
    if not valid:
        print("\n所有调用均失败，请确认本地 llama.cpp 或 DEEPSEEK_API_KEY 可用。")
        return 1

    cat_acc = sum(x["cat_ok"] for x in valid) / len(valid)
    exact_rate = sum(x["exact"] for x in valid) / len(valid)
    mean_p = sum(x["p"] for x in valid) / len(valid)
    mean_r = sum(x["r"] for x in valid) / len(valid)

    print("=" * 92)
    print(f"有效样本: {len(valid)}/{total}")
    print(f"类别准确率 (category_accuracy): {cat_acc:.1%}")
    print(f"工具完全匹配率 (tool_exact_match): {exact_rate:.1%}")
    print(f"工具精确率均值 (tool_precision): {mean_p:.1%}   ← 越低表示多调了无关工具")
    print(f"工具召回率均值 (tool_recall)   : {mean_r:.1%}   ← 越低表示漏调了必要工具")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
