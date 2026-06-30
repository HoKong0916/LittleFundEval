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

# ── llama.cpp 服务连接 ──────────────────────────────────────
# llama.cpp server 的 OpenAI 兼容 API 地址
# 启动 llama-server 后它会暴露一个与 OpenAI 格式兼容的 HTTP 服务
# /v1 是 API 版本前缀，openai SDK 需要这个路径
LLAMA_CPP_BASE_URL = os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:9856/v1")


# ── DeepSeek（评估 + 查询）─────────────────────────────────────
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")