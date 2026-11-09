"""LLM 클라이언트. 테스트=FakeLLM, 개발·평가=SessionLLM(응답 파일, 과금 0), 운영=OpenAILLM.

SessionLLM이 답하는 프롬프트는 운영 GPT에 보낼 문자열과 같다. 응답이 없으면 에러를 내고, 조용히 대체하지 않는다.
"""
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from dotenv import dotenv_values

from . import config


class MissingResponse(Exception):
    def __init__(self, key, task=None):
        super().__init__(f"세션 응답이 없습니다: {task}/{key}")
        self.key = key
        self.task = task


class LLMClient(Protocol):
    def complete_json(self, task: str, system: str, user: str, schema: dict) -> dict: ...


def request_key(task, system, user, schema):
    raw = json.dumps([task, system, user, schema], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class FakeLLM:
    def __init__(self, responses: "dict | Callable"):
        self.responses = responses
        self.calls = []

    def complete_json(self, task, system, user, schema):
        key = request_key(task, system, user, schema)
        self.calls.append({"task": task, "system": system, "user": user, "schema": schema, "key": key})
        if callable(self.responses):
            return self.responses(task, system, user, schema)
        if key in self.responses:
            return self.responses[key]
        if task not in self.responses:
            raise MissingResponse(key, task)
        answer = self.responses[task]
        return answer.pop(0) if isinstance(answer, list) else answer


class SessionLLM:
    """export: 응답이 있으면 돌려주고, 없으면 요청 파일을 쓰고 MissingResponse. run: 응답이 없으면 MissingResponse."""

    def __init__(self, root=config.SESSION_DIR, mode="run"):
        assert mode in ("run", "export"), mode
        self.root = Path(root)
        self.mode = mode

    def complete_json(self, task, system, user, schema):
        key = request_key(task, system, user, schema)
        response = self.root / "responses" / task / f"{key}.json"
        if response.exists():
            return json.loads(response.read_text(encoding="utf-8"))
        if self.mode == "export":
            request = self.root / "requests" / task / f"{key}.json"
            request.parent.mkdir(parents=True, exist_ok=True)
            request.write_text(json.dumps({"task": task, "system": system, "user": user, "schema": schema},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        raise MissingResponse(key, task)


class OpenAILLM:
    """운영용. Responses API 구조화 출력. 키는 config.OPENAI_ENV에서 처음 호출할 때 읽는다."""

    def __init__(self, model="gpt-6-luna", effort="low"):
        self.model = model
        self.effort = effort
        self._client = None
        self._local = threading.local()  # 병렬 호출 때 스레드마다 자기 호출의 토큰만 본다

    @property
    def last_usage(self):
        return getattr(self._local, "usage", None)

    @last_usage.setter
    def last_usage(self, value):
        self._local.usage = value

    def _get(self):
        if self._client is None:
            from openai import OpenAI
            key = dotenv_values(config.OPENAI_ENV).get("OPENAI_API_KEY") if config.OPENAI_ENV.exists() else None
            self._client = OpenAI(api_key=key) if key else OpenAI()
        return self._client

    def complete_json(self, task, system, user, schema):
        res = self._get().responses.create(
            model=self.model, reasoning={"effort": self.effort},
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": task, "strict": True, "schema": schema}})
        self.last_usage = usage_of(res)
        return json.loads(res.output_text)


class AnthropicLLM:
    """Claude(Haiku) 비교용. 구조화 출력(json_schema), system 프롬프트 캐시. 키는 config.OPENAI_ENV(.env)의 ANTHROPIC_API_KEY."""

    def __init__(self, model="claude-haiku-5-5", effort="low", max_tokens=16000):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = None
        self._local = threading.local()

    @property
    def last_usage(self):
        return getattr(self._local, "usage", None)

    @last_usage.setter
    def last_usage(self, value):
        self._local.usage = value

    def _get(self):
        if self._client is None:
            import anthropic
            key = dotenv_values(config.OPENAI_ENV).get("ANTHROPIC_API_KEY") if config.OPENAI_ENV.exists() else None
            self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    def complete_json(self, task, system, user, schema):
        res = self._get().messages.create(
            model=self.model, max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": claude_schema(schema)}})
        self.last_usage = anthropic_usage_of(res)
        if res.stop_reason in ("max_tokens", "refusal"):
            raise RuntimeError(f"Claude 응답이 완결되지 않았습니다: {task}/{res.stop_reason}")
        return json.loads(next(b.text for b in res.content if b.type == "text"))


def claude_schema(schema):
    """Claude 구조화 출력이 받지 않는 배열 제약(maxItems, 2 이상의 minItems)을 뺀 사본. 개수는 verify.check가 검사한다."""
    if isinstance(schema, list):
        return [claude_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: claude_schema(v) for k, v in schema.items() if k != "maxItems"}
    if isinstance(out.get("minItems"), int) and out["minItems"] > 1:
        del out["minItems"]
    return out


def anthropic_usage_of(res):
    """Messages API usage → OpenAI와 같은 키. input은 캐시 읽기·쓰기를 포함한 입력 합계, output은 thinking 포함."""
    u = getattr(res, "usage", None)
    if u is None:
        return None
    read = getattr(u, "cache_read_input_tokens", None) or 0
    write = getattr(u, "cache_creation_input_tokens", None) or 0
    return {"input": u.input_tokens + read + write, "output": u.output_tokens, "reasoning": None,
            "cached": read, "cache_write": write}


def usage_of(res):
    """Responses API usage → {input, output(추론 포함), reasoning, cached}. 없으면 None."""
    u = getattr(res, "usage", None)
    if u is None:
        return None
    return {"input": u.input_tokens, "output": u.output_tokens,
            "reasoning": getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", None),
            "cached": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", None)}


class Meter:
    """클라이언트를 감싸 호출마다 걸린 시간과 토큰(클라이언트가 last_usage를 주면)을 calls에 남긴다."""

    def __init__(self, client):
        self.client = client
        self.calls = []

    def complete_json(self, task, system, user, schema):
        if hasattr(self.client, "last_usage"):
            self.client.last_usage = None
        start = time.perf_counter()
        out = self.client.complete_json(task, system, user, schema)
        usage = getattr(self.client, "last_usage", None) or {}
        self.calls.append({"task": task, "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                           **{k: usage.get(k) for k in ("input", "output", "reasoning", "cached", "cache_write")}})
        return out
