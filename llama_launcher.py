import os
import subprocess
import time
import httpx
from config import (
    LLAMA_SERVER_PATH,
    LLAMA_MODEL_PATH,
    LLAMA_SERVER_HOST,
    LLAMA_SERVER_PORT,
    LLAMA_N_GPU_LAYERS,
    LLAMA_CTX_SIZE,
    LLAMA_FLASH_ATTENTION,
    LLAMA_PARALLEL,
    LLAMA_SPEC_TYPE,
    LLAMA_SPEC_DRAFT_N_MAX,
    LLAMA_THINKING
)

# 同进程内 terminate 用
_server_process: subprocess.Popen | None = None

# PID 文件：跨进程终止时用
_PID_FILE = os.path.join(os.path.dirname(__file__), "llama_server.pid")


def _read_pid() -> int | None:
    try:
        with open(_PID_FILE) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def start_server() -> bool:
    """启动 llama-server 并等待就绪"""
    global _server_process

    if is_running():
        print("[llama-launcher] 服务器已在运行")
        return True

    cmd = [
        LLAMA_SERVER_PATH,
        "-m", LLAMA_MODEL_PATH,
        "--host", LLAMA_SERVER_HOST,
        "--port", str(LLAMA_SERVER_PORT),
        "--n-gpu-layers", str(LLAMA_N_GPU_LAYERS),
        "-c", str(LLAMA_CTX_SIZE),
        "-fa", LLAMA_FLASH_ATTENTION,
        "--kv-unified",
        "-np", str(LLAMA_PARALLEL),
        "--spec-type", LLAMA_SPEC_TYPE,
        "--spec-draft-n-max", str(LLAMA_SPEC_DRAFT_N_MAX),
        "--reasoning", str(LLAMA_THINKING)
    ]

    print(f"[llama-launcher] 启动 llama-server (端口 {LLAMA_SERVER_PORT})...")

    _server_process = subprocess.Popen(
        cmd,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )

    # 写入 PID 文件，方便其他进程定位
    with open(_PID_FILE, "w") as f:
        f.write(str(_server_process.pid))

    health_url = f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/health"
    for i in range(60):
        # poll() 返回 None → 进程还在跑；返回数字 → 已退出，数字即 exit code
        exit_code = _server_process.poll()
        if exit_code is not None:
            # 进程挂了，清理现场
            _server_process = None
            os.remove(_PID_FILE)
            raise RuntimeError(f"llama-server 意外退出 (exit code: {exit_code})")

        try:
            if httpx.get(health_url, timeout=1).status_code == 200:
                print(f"[llama-launcher] 服务器就绪 (耗时约 {i + 1}s)")
                return True
        except Exception:
            pass

        time.sleep(1)

    # 超时：优雅终止，清理现场
    _server_process.terminate()
    _server_process.wait()
    _server_process = None
    os.remove(_PID_FILE)
    raise RuntimeError("llama-server 启动超时 (60s)")


def stop_server() -> None:
    """终止 llama-server。优先优雅退出，跨进程时 fallback 到 taskkill"""
    global _server_process

    # 同进程内：优雅终止
    if _server_process is not None:
        print("[llama-launcher] 终止 llama-server (同进程 terminate)...")
        _server_process.terminate()
        _server_process.wait()
        _server_process = None
        if os.path.exists(_PID_FILE):
            os.remove(_PID_FILE)
        return

    # 跨进程：通过 PID 文件强杀
    pid = _read_pid()
    if pid is None:
        print("[llama-launcher] 未找到运行中的服务器 PID")
        return

    print(f"[llama-launcher] 终止 llama-server (跨进程 taskkill, PID: {pid})...")
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    os.remove(_PID_FILE)
    print("[llama-launcher] 已终止")


def is_running() -> bool:
    """检查 llama-server /health 端点"""
    try:
        r = httpx.get(
            f"http://{LLAMA_SERVER_HOST}:{LLAMA_SERVER_PORT}/health",
            timeout=1,
        )
        return r.status_code == 200
    except Exception:
        return False
