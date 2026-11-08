"""OpenAI 호출 계층. 임베딩과 LLM 응답을 파일에 저장해, 같은 입력은 다시 호출하지 않는다.

- embed():    out/embeddings.jsonl, 키는 sha256(모델|문장)
- complete(): out/llm_cache/<tag>.jsonl, 키는 sha256(모델|effort|지시문|출력 형식|입력)
테스트는 api 인자에 가짜 호출 함수를 넣는다. API 키는 ../.env에서 읽고 출력하지 않는다.
"""
import hashlib
import json
import time

from pydantic import BaseModel

from . import config

EMBED_CACHE = config.OUT / "embeddings.jsonl"
LLM_CACHE_DIR = config.OUT / "llm_cache"
EMBED_BATCH = 256
USAGE_LOG: list[dict] = []          # complete() 호출마다 한 줄. ask()가 구간 합계를 낸다
_embeddings: dict | None = None     # 임베딩 저장 파일을 메모리에 올린 것


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _embed_key(text: str) -> str:
    return _key(config.EMBED_MODEL, text)


def _client():
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(config.OPENAI_ENV)
    return OpenAI()


def _stored_embeddings() -> dict:
    global _embeddings
    if _embeddings is None:
        _embeddings = {}
        if EMBED_CACHE.exists():
            with EMBED_CACHE.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        _embeddings[rec["key"]] = rec["vector"]
    return _embeddings


def cached_embeddings(texts: list[str]) -> dict[str, list[float]]:
    """저장 파일에 이미 있는 문장만 돌려준다. 호출하지 않는다."""
    store = _stored_embeddings()
    return {t: store[_embed_key(t)] for t in texts if _embed_key(t) in store}


def _embed_api(texts: list[str]) -> list[list[float]]:
    client, vectors = _client(), []
    for i in range(0, len(texts), EMBED_BATCH):
        response = client.embeddings.create(model=config.EMBED_MODEL, input=texts[i:i + EMBED_BATCH])
        vectors += [item.embedding for item in response.data]
    return vectors


def embed(texts: list[str], api=None) -> list[list[float]]:
    """문장을 임베딩한다. 저장된 문장은 파일에서 꺼내고, 없는 문장만 호출해 저장한다."""
    store = _stored_embeddings()
    missing = list(dict.fromkeys(t for t in texts if _embed_key(t) not in store))
    if missing:
        vectors = (api or _embed_api)(missing)
        if len(vectors) != len(missing):
            raise RuntimeError(f"임베딩 {len(missing)}건을 요청했는데 {len(vectors)}건이 왔습니다.")
        EMBED_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with EMBED_CACHE.open("a", encoding="utf-8") as f:
            for text, vector in zip(missing, vectors):
                store[_embed_key(text)] = list(vector)
                f.write(json.dumps({"key": _embed_key(text), "vector": list(vector)}) + "\n")
    return [store[_embed_key(t)] for t in texts]


def count_tokens(texts: list[str]) -> int:
    import tiktoken
    encoding = tiktoken.get_encoding("cl100k_base")
    return sum(len(encoding.encode(t)) for t in texts)


def _complete_api(instructions: str, payload: str, schema: type[BaseModel]) -> tuple[dict, dict]:
    response = _client().responses.parse(model=config.CHAT_MODEL, instructions=instructions, input=payload,
                                         text_format=schema, reasoning={"effort": config.EFFORT})
    if response.output_parsed is None:
        raise RuntimeError("LLM이 정해진 형식의 답을 돌려주지 않았습니다.")
    usage = getattr(response, "usage", None)
    return response.output_parsed.model_dump(), {"tokens_in": getattr(usage, "input_tokens", 0) or 0,
                                                 "tokens_out": getattr(usage, "output_tokens", 0) or 0}


def _stored_completions(path) -> dict:
    records = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                records[rec["key"]] = rec
    return records


def complete(instructions: str, payload: str, schema: type[BaseModel], tag: str, api=None) -> BaseModel:
    """LLM을 불러 schema 형식의 답을 받는다. 같은 (모델, 지시문, 형식, 입력)은 저장된 답을 돌려준다."""
    shape = json.dumps(schema.model_json_schema(), sort_keys=True, ensure_ascii=False)
    key = _key(config.CHAT_MODEL, config.EFFORT, instructions, shape, payload)
    path = LLM_CACHE_DIR / f"{tag}.jsonl"
    started = time.time()
    record = _stored_completions(path).get(key)
    cached = record is not None
    if not cached:
        output, usage = (api or _complete_api)(instructions, payload, schema)
        record = {"key": key, "output": output, "usage": usage}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    USAGE_LOG.append({"tag": tag, "cached": cached, "latency_s": round(time.time() - started, 3),
                      "tokens_in": record["usage"].get("tokens_in", 0), "tokens_out": record["usage"].get("tokens_out", 0)})
    return schema.model_validate(record["output"])
