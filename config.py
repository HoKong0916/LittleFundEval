import os

# ── 模型加载 ────────────────────────────────────────────────
# 本地 GGUF 模型文件路径
LLAMA_MODEL_PATH = os.getenv(
    "LLAMA_MODEL_PATH",
    "D:\\QwenModels\\Qwen3.5-9B-UD-Q4_K_XL.gguf",
)

# ── llama.cpp 进程管理 ──────────────────────────────────────
# llama-server 可执行文件路径
LLAMA_SERVER_PATH = os.getenv(
    "LLAMA_SERVER_PATH",
    "D:\\llamacpp\\llama-server.exe",
)

# 监听地址与端口
LLAMA_SERVER_HOST = os.getenv("LLAMA_SERVER_HOST", "127.0.0.1")
LLAMA_SERVER_PORT = int(os.getenv("LLAMA_SERVER_PORT", "9856"))

# ── 推理参数 ────────────────────────────────────────────────
# GPU 层数：卸载到 GPU 的 transformer 层数，-1 = 自动全部卸载
LLAMA_N_GPU_LAYERS = int(os.getenv("LLAMA_N_GPU_LAYERS", "-1"))

# 上下文窗口大小（token 数），越大显存占用越高
LLAMA_CTX_SIZE = int(os.getenv("LLAMA_CTX_SIZE", "32768"))

# Flash Attention：数学等价优化，减少 KV Cache 显存约 30-50%，无损推理质量
LLAMA_FLASH_ATTENTION = os.getenv("LLAMA_FLASH_ATTENTION", "on")

# 并行请求数：1 表示串行处理，适合单用户本地部署
LLAMA_PARALLEL = int(os.getenv("LLAMA_PARALLEL", "1"))

# 是否开启思考模式：默认不开
LLAMA_THINKING = os.getenv("LLAMA_THINKING", "off")

# ── 投机解码 (Speculative Decoding) ─────────────────────────
# Qwen3.5 原生支持 MTP (Multi-Token Prediction)，一次前向预测多个未来 token
#   llama.cpp PR #23269 已将 --spec-draft-n-max 默认值从 16 下调为 3
#   3 是吞吐量和接受率的最佳平衡点（接受率约 72%，高于 6 的 ~68%）
LLAMA_SPEC_TYPE = os.getenv("LLAMA_SPEC_TYPE", "draft-mtp")
LLAMA_SPEC_DRAFT_N_MAX = int(os.getenv("LLAMA_SPEC_DRAFT_N_MAX", "2"))

# ── 降级策略 ────────────────────────────────────────────────
# llama-server 不可用时是否自动切换 DeepSeek 处理 local_chat 调用
# 设为 "0" 禁止降级（连不上本地 LLM 直接报错）
LLM_FALLBACK_TO_CLOUD = os.getenv("LLM_FALLBACK_TO_CLOUD", "1") == "1"

# ── llama.cpp 服务连接 ──────────────────────────────────────
# llama.cpp server 的 OpenAI 兼容 API 地址
# 启动 llama-server 后它会暴露一个与 OpenAI 格式兼容的 HTTP 服务
# /v1 是 API 版本前缀，openai SDK 需要这个路径
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

# ── 调试 trace ──────────────────────────────────────────────
# 设为 "1" 时终端实时展示人类可读的 ReAct/REWOO 进度提示
# trace 数据（含 Thought/Action/Observation 原文）始终写入 Redis
DEBUG_TRACE = os.getenv("DEBUG_TRACE", "1") == "1"