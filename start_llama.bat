@echo off
REM ============================================================
REM  llama-server 一键启动脚本
REM  与 config.py 中的默认值保持一致
REM  可通过同名环境变量覆盖各项配置
REM ============================================================

REM -- 模型文件路径 --
if not defined LLAMA_MODEL_PATH set "LLAMA_MODEL_PATH=D:\QwenModels\Qwen3.5-9B-UD-Q4_K_XL.gguf"

REM -- llama-server 可执行文件 --
if not defined LLAMA_SERVER_PATH set "LLAMA_SERVER_PATH=D:\llamacpp\llama-server.exe"

REM -- 监听地址与端口 --
if not defined LLAMA_SERVER_HOST set "LLAMA_SERVER_HOST=127.0.0.1"
if not defined LLAMA_SERVER_PORT set "LLAMA_SERVER_PORT=9856"

REM -- GPU 层数：-1 = 全部卸载到 GPU --
if not defined LLAMA_N_GPU_LAYERS set "LLAMA_N_GPU_LAYERS=-1"

REM -- 上下文窗口大小 --
if not defined LLAMA_CTX_SIZE set "LLAMA_CTX_SIZE=32768"

REM -- 并行槽位数 --
if not defined LLAMA_PARALLEL set "LLAMA_PARALLEL=1"

echo ============================================================
echo   llama-server 启动
echo   模型: %LLAMA_MODEL_PATH%
echo   端口: %LLAMA_SERVER_PORT%
echo   GPU层: %LLAMA_N_GPU_LAYERS%
echo   上下文: %LLAMA_CTX_SIZE%
echo   并行槽位: %LLAMA_PARALLEL%
echo ============================================================
echo.

"%LLAMA_SERVER_PATH%" ^
  -m "%LLAMA_MODEL_PATH%" ^
  --host %LLAMA_SERVER_HOST% ^
  --port %LLAMA_SERVER_PORT% ^
  --n-gpu-layers %LLAMA_N_GPU_LAYERS% ^
  -c %LLAMA_CTX_SIZE% ^
  -fa ^
  --kv-unified ^
  -np %LLAMA_PARALLEL% ^
  --spec-type draft-mtp ^
  --spec-draft-n-max 2 ^
  --reasoning off

echo.
echo llama-server 已退出。
pause
