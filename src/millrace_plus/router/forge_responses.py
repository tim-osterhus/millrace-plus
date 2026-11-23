"""OpenAI Responses client code embedded in the captured v3 Forge worker."""

import os
import re

RESPONSES_URL = "https://api.openai.com/v1/responses"
RESPONSES_TOOLS = frozenset({"list_dir", "read_file", "finish"})
RESPONSES_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)
RESPONSES_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
RESPONSES_KEY = re.compile(r"sk-[A-Za-z0-9_-]{1,1024}")
RESPONSES_BODY_MAX = 131072
RESPONSES_OUTPUT_TOKENS = 1024


def _response_protocol(condition):
    if not condition:
        raise _Failure("forge_backend_protocol")  # noqa: F821 (standalone worker)


def _responses_request_backend(value):
    backend = _exact(value, ("kind", "auth", "model", "reasoning_effort"))  # noqa: F821
    auth = _exact(backend["auth"], ("kind", "name"))  # noqa: F821
    if (
        backend["kind"] != "openai_responses"
        or auth["kind"] != "env"
        or not RESPONSES_MODEL.fullmatch(_text(backend["model"]))  # noqa: F821
        or _text(backend["reasoning_effort"]) not in RESPONSES_EFFORTS  # noqa: F821
    ):
        raise _Failure("forge_request_invalid")  # noqa: F821
    _env_name(auth["name"])  # noqa: F821


def _responses_key():
    key = os.environ.pop("MILLRACE_FORGE_RESPONSES_KEY", "")
    if not RESPONSES_KEY.fullmatch(key):
        raise _Failure("forge_backend_config_invalid")  # noqa: F821
    return key


def _responses_client(forge, backend, budgets, key):
    import httpx

    return _ResponsesClient(
        forge.ToolCall,
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}"},
            timeout=float(budgets["http_timeout_seconds"]),
            trust_env=False,
            follow_redirects=False,
        ),
        backend["model"],
        backend["reasoning_effort"],
        budgets["max_iterations"],
    )


class _ResponsesClient:
    """Implement the nonstreaming Forge send seam for the three worker tools."""

    api_format = "openai"

    def __init__(self, tool_call, http, model, effort, limit):
        self.tool_call = tool_call
        self.http = http
        self.model = model
        self.effort = effort
        self.limit = limit
        self.requests = []
        self.prompt = None
        self.history = []
        self.pending = None
        self.call_ids = set()

    async def aclose(self):
        await self.http.aclose()

    def _input(self, messages):
        prompt = [
            {"role": msg["role"], "content": msg["content"]} for msg in messages[:2]
        ]
        _response_protocol(
            [entry["role"] for entry in prompt] == ["system", "user"]
            and all(type(entry["content"]) is str for entry in prompt)
            and len(messages) == 2 + 2 * len(self.requests)
        )
        if self.prompt is None:
            self.prompt = prompt
            return prompt[:]
        _response_protocol(self.prompt == prompt and self.pending is not None)
        assistant, result = messages[-2:]
        calls = assistant.get("tool_calls")
        _response_protocol(
            assistant.get("role") == "assistant"
            and isinstance(calls, list)
            and len(calls) == 1
            and result.get("role") == "tool"
        )
        call = calls[0]
        provider_call, args = self.pending
        _response_protocol(
            provider_call["name"] in {"list_dir", "read_file"}
            and bool(call.get("id"))
            and result.get("tool_call_id") == call["id"]
            and result.get("name") == provider_call["name"] == call["function"]["name"]
            and _strict_json(call["function"]["arguments"].encode("utf-8")) == args  # noqa: F821
            and type(result.get("content")) is str
        )
        self.history.append(
            {
                "type": "function_call_output",
                "call_id": provider_call["call_id"],
                "output": result["content"],
            }
        )
        self.pending = None
        return self.prompt + self.history

    async def send(
        self,
        messages,
        tools=None,
        sampling=None,
        passthrough=None,
        inbound_anthropic_body=None,
        raw_openai_tools=None,
        extra_headers=None,
    ):
        import httpx

        if len(self.requests) >= self.limit:
            raise _Failure("forge_max_iterations")  # noqa: F821
        _response_protocol(
            not (
                sampling
                or passthrough
                or inbound_anthropic_body
                or raw_openai_tools
                or extra_headers
            )
            and tools is not None
            and len(tools) == len(RESPONSES_TOOLS)
            and {tool.name for tool in tools} == RESPONSES_TOOLS
        )
        try:
            input_items = self._input(messages)
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise _Failure("forge_backend_protocol") from exc  # noqa: F821
        body = {
            "model": self.model,
            "input": input_items,
            "tools": [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.get_json_schema(),
                    "strict": False,
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": {"effort": self.effort},
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": RESPONSES_OUTPUT_TOKENS,
        }
        receipt = {
            "sent_effort": body["reasoning"]["effort"],
            "http_status": None,
            "response_model": None,
            "echoed_effort": None,
            "tool": None,
        }
        self.requests.append(receipt)
        received = bytearray()
        try:
            async with self.http.stream("POST", RESPONSES_URL, json=body) as response:
                receipt["http_status"] = response.status_code
                if response.status_code != 200:
                    raise _Failure("forge_backend_http_error")  # noqa: F821
                async for part in response.aiter_bytes(chunk_size=4096):
                    _response_protocol(len(received) + len(part) <= RESPONSES_BODY_MAX)
                    received.extend(part)
        except httpx.TimeoutException as exc:
            raise _Failure("forge_backend_timeout") from exc  # noqa: F821
        except httpx.RequestError as exc:
            raise _Failure("forge_backend_transport") from exc  # noqa: F821
        try:
            return self._output(_strict_json(bytes(received)), receipt)  # noqa: F821
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise _Failure("forge_backend_protocol") from exc  # noqa: F821

    def _output(self, payload, receipt):
        _response_protocol(
            isinstance(payload, dict)
            and payload.get("object") == "response"
            and payload.get("status") == "completed"
            and payload.get("error") is None
            and payload.get("incomplete_details") is None
        )
        model = payload.get("model")
        _response_protocol(
            isinstance(model, str)
            and (
                model == self.model
                or re.fullmatch(re.escape(self.model) + r"-\d{4}-\d{2}-\d{2}", model)
            )
        )
        receipt["response_model"] = model
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
            effort = reasoning["effort"]
            _response_protocol(isinstance(effort, str) and effort in RESPONSES_EFFORTS)
            receipt["echoed_effort"] = effort
        output = payload.get("output")
        _response_protocol(isinstance(output, list))
        calls = []
        for item in output:
            kind = item.get("type")
            if kind == "function_call":
                calls.append(item)
            elif kind == "reasoning":
                encrypted = item.get("encrypted_content")
                _response_protocol(isinstance(encrypted, str) and bool(encrypted))
            elif kind == "message":
                content = item.get("content")
                _response_protocol(
                    item.get("role") == "assistant"
                    and isinstance(content, list)
                    and all(part.get("type") == "output_text" for part in content)
                )
            else:
                _response_protocol(False)
        _response_protocol(len(calls) == 1)
        call = calls[0]
        name, call_id, arguments = (
            call.get("name"),
            call.get("call_id"),
            call.get("arguments"),
        )
        _response_protocol(
            name in RESPONSES_TOOLS
            and isinstance(call_id, str)
            and bool(call_id)
            and call_id not in self.call_ids
            and isinstance(arguments, str)
            and call.get("status") in (None, "completed")
        )
        args = _strict_json(arguments.encode("utf-8"))  # noqa: F821
        _response_protocol(isinstance(args, dict))
        self.call_ids.add(call_id)
        self.history.extend(output)
        self.pending = (call, args)
        receipt["tool"] = name
        return [self.tool_call(tool=name, args=args)]
