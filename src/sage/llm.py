"""Streaming client for an OpenAI-style llama-server."""
import json

import requests


# --------------------------------------------------------------------------- LLM streaming
def stream_llm(args, messages: list, tool_schemas):
    """Yields ("content", text) pieces, then one ("tool_calls", [...]) if the model asked for tools."""
    payload = {
        "model": args.model,
        "messages": messages,
        "stream": True,
        "cache_prompt": True,      # reuse the unchanged start of the prompt (system prompt + history) between turns
        "max_tokens": args.max_tokens,
        "chat_template_kwargs": {"enable_thinking": args.thinking},
    }
    if tool_schemas:
        payload["tools"] = tool_schemas
    calls = {}
    with requests.post(args.url, json=payload, stream=True, timeout=(5, 120)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"server returned {r.status_code}: {r.text[:300]}")
        for line in (b.decode("utf-8", "replace") for b in r.iter_lines()):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except (ValueError, KeyError, IndexError):
                continue
            # delta["reasoning_content"] (thinking) is deliberately ignored: never spoken, never shown
            if delta.get("content"):
                yield ("content", delta["content"])
            for tc in delta.get("tool_calls") or []:
                i = tc.get("index", 0)
                e = calls.setdefault(i, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    e["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    if not e["name"]:
                        yield ("tool_start", fn["name"])
                    e["name"] = fn["name"]
                if fn.get("arguments"):
                    e["arguments"] += fn["arguments"]
    if calls:
        out = [calls[i] for i in sorted(calls)]
        for i, c in enumerate(out):
            c["id"] = c["id"] or f"call_{i}"
            c["arguments"] = c["arguments"].strip() or "{}"
        yield ("tool_calls", out)
