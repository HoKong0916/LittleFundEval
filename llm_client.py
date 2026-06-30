import json, asyncio
from openai import OpenAI, AsyncOpenAI
from config import LLAMA_CPP_BASE_URL, DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL

_client = OpenAI(base_url=LLAMA_CPP_BASE_URL, api_key="not-needed")
_deepseek_client = AsyncOpenAI(base_url=DEEPSEEK_BASE_URL, api_key=DEEPSEEK_API_KEY)
_model_name: str | None = None


def _get_model_name() -> str:
    global _model_name
    if _model_name is None:
        _model_name = next(iter(_client.models.list())).id
    return _model_name


def local_chat(messages: list[dict], temperature: float = 0.0) -> str:
    """本地 llama.cpp — 轻量分类。"""
    from llama_launcher import start_server
    start_server()
    response = _client.chat.completions.create(
        model=_get_model_name(),
        messages=messages,
        temperature=temperature
    )
    # stop_server()
    return response.choices[0].message.content


async def cloud_chat(
    messages: list[dict],
    temperature: float = 0.0,
    tools: list[dict] | None = None,
):
    """云端 DeepSeek — 流式评估，支持 function calling。

    yield:
      {"type": "text", "content": "增量文本"}
      {"type": "tool_calls", "calls": [{"id": "...", "name": "...", "arguments": {...}}]}
      {"type": "done", "finish_reason": "stop" | "tool_calls" | "cancelled" | "error"}
    """
    kwargs: dict = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "extra_body":{"thinking": {"type": "disabled"}}
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    stream = await _deepseek_client.chat.completions.create(**kwargs)

    tool_buf: dict[int, dict] = {}
    try:
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

        if tool_buf:
            calls = [tool_buf[i] for i in sorted(tool_buf)]
            yield {
                "type": "tool_calls",
                "calls": [
                    {"id": b["id"], "name": b["name"], "arguments": json.loads(b["args"])}
                    for b in calls
                ],
            }
            yield {"type": "done", "finish_reason": "tool_calls"}
        else:
            yield {"type": "done", "finish_reason": "stop"}

    except asyncio.CancelledError:
        yield {"type": "done", "finish_reason": "cancelled"}
    except Exception:
        yield {"type": "done", "finish_reason": "error"}
        raise
    finally:
        await stream.response.aclose()
