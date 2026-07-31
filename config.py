"""全局配置中心 —— 集中管理 LLM、Redis、鉴权、限流、飞书、日志等配置。

所有配置项均支持环境变量覆盖，未设置时使用默认值。
"""

import os

# ── 降级策略 ────────────────────────────────────────────────
# llama-server 不可用时是否自动切换 DeepSeek 处理 local_chat 调用
# 设为 "0" 禁止降级（连不上本地 LLM 直接报错）
LLM_FALLBACK_TO_CLOUD = os.getenv("LLM_FALLBACK_TO_CLOUD", "1") == "1"

# ── llama.cpp 服务连接 ──────────────────────────────────────
# llama.cpp server 的 OpenAI 兼容 API 地址（/v1 前缀供 openai SDK 使用）
LLAMA_CPP_BASE_URL = os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:9856/v1")


# ── DeepSeek（评估 + 查询）─────────────────────────────────────
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

# ── 对话记忆 ────────────────────────────────────────────────
import tiktoken

# tiktoken 编码器：o200k_base 与 DeepSeek tokenizer 高度接近
_TOKEN_ENC = tiktoken.get_encoding("o200k_base")

# 单会话 token 超此阈值触发摘要化
MAX_TOKEN_THRESHOLD = 10000


def count_tokens(text: str) -> int:
    """使用 tiktoken (o200k_base) 精确计算 token 数。"""
    return len(_TOKEN_ENC.encode(text))


# ── Redis（会话短期记忆 + trace 日志）────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# ── API Token 管理 ──────────────────────────────────────────
# SQLite 数据库路径，存储 api_tokens 表（token / user_id / tier / 过期 / 吊销）
# 首次启动时自动建表 + 种子 admin token
TOKEN_DB_PATH = os.getenv("TOKEN_DB_PATH", "./data/tokens.db")

# ── API 限流（滑动窗口日志算法）────────────────────────────
# visitor: 每分钟 RATE_LIMIT_MAX_REQUESTS 次
# admin: 不限流（core/rate_limit.py 中 tier == "admin" 直接放行）
# Redis 不可用时降级放行，不阻塞业务
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "5"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# ── LLM token 日预算（cloud_chat 每用户每天输入+输出合计上限）──
DAILY_TOKEN_BUDGET = int(os.getenv("DAILY_TOKEN_BUDGET", "550000"))

# ── 飞书自建应用 ──────────────────────────────────────────
# 飞书机器人 App ID / App Secret（从飞书开放平台获取）
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
# false 时 FastAPI 正常启动但不连接飞书，方便本地开发调试
FEISHU_ENABLED = os.getenv("FEISHU_ENABLED", "true").lower() == "true"

# ── 调试 trace ──────────────────────────────────────────────
# 设为 "1" 时终端实时展示人类可读的 ReAct/REWOO 进度提示
# trace 数据（含 Thought/Action/Observation 原文）始终写入 Redis
DEBUG_TRACE = os.getenv("DEBUG_TRACE", "1") == "1"

# ── 文件日志 ────────────────────────────────────────────────
# 配置则同时输出到终端和文件（RotatingFileHandler 轮转），不配置仅输出终端
# Docker 部署建议挂载日志目录：docker run -v /var/log/app:/app/data ...
LOG_FILE = os.getenv("LOG_FILE", "./data/app.log")
# 单个日志文件最大字节数（默认 10MB），超过后轮转为 .1 .2 ...
LOG_FILE_MAX_BYTES = int(os.getenv("LOG_FILE_MAX_BYTES", str(10 * 1024 * 1024)))
# 保留的历史日志文件份数
LOG_FILE_BACKUP_COUNT = int(os.getenv("LOG_FILE_BACKUP_COUNT", "5"))