chcp 65001 > nul
@echo off
REM ============================================================
REM  llama-server start script
REM  Defaults match config.py. Override via env vars.
REM ============================================================

REM -- Model file --
if not defined LLAMA_MODEL_PATH set "LLAMA_MODEL_PATH=D:\QwenModels\Qwen3.5-9B-UD-Q4_K_XL.gguf"

REM -- llama-server binary --
if not defined LLAMA_SERVER_PATH set "LLAMA_SERVER_PATH=D:\llamacpp\llama-server.exe"

REM -- Host & port --
if not defined LLAMA_SERVER_HOST set "LLAMA_SERVER_HOST=127.0.0.1"
if not defined LLAMA_SERVER_PORT set "LLAMA_SERVER_PORT=9856"

REM -- GPU layers (-1 = all offload to GPU) --
if not defined LLAMA_N_GPU_LAYERS set "LLAMA_N_GPU_LAYERS=-1"

REM -- Context window size --
if not defined LLAMA_CTX_SIZE set "LLAMA_CTX_SIZE=32768"

REM -- Parallel slots --
if not defined LLAMA_PARALLEL set "LLAMA_PARALLEL=1"

echo ============================================================
echo   llama-server starting
echo   Model : %LLAMA_MODEL_PATH%
echo   Port  : %LLAMA_SERVER_PORT%
echo   GPU   : %LLAMA_N_GPU_LAYERS%
echo   Ctx   : %LLAMA_CTX_SIZE%
echo   Slots : %LLAMA_PARALLEL%
echo ============================================================
echo.

"%LLAMA_SERVER_PATH%" ^
  -m "%LLAMA_MODEL_PATH%" ^
  --host %LLAMA_SERVER_HOST% ^
  --port %LLAMA_SERVER_PORT% ^
  --n-gpu-layers %LLAMA_N_GPU_LAYERS% ^
  -c %LLAMA_CTX_SIZE% ^
  -fa auto ^
  --kv-unified ^
  -np %LLAMA_PARALLEL% ^
  --spec-type draft-mtp ^
  --spec-draft-n-max 2 ^
  --reasoning off

echo.
echo llama-server exited.
pause
