"""python -m rxrag.cli recommend <patient.json> [--llm session|openai]"""
import argparse
import json
import sys
from pathlib import Path

from .llm import MissingResponse, OpenAILLM, SessionLLM
from .recommend import recommend


def client_for(name):
    return OpenAILLM() if name == "openai" else SessionLLM(mode="run")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rxrag")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("recommend", help="환자 JSON 하나로 성분 3개 추천")
    p.add_argument("patient", type=Path)
    p.add_argument("--llm", choices=["session", "openai"], default="session")
    args = parser.parse_args(argv)
    patient = json.loads(args.patient.read_text(encoding="utf-8"))
    try:
        out = recommend(patient, client_for(args.llm))
    except MissingResponse as e:
        sys.exit(f"세션 응답이 없습니다({e.task}/{e.key}). out/session/requests를 내보낸 뒤 응답을 작성하세요.")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
