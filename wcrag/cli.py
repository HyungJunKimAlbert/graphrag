"""명령줄 진입점.

  python -m wcrag.cli status
  python -m wcrag.cli ask "<질문>" [--patient <환자 JSON 파일>] [--mode auto|vector|local]
"""
import argparse
import json
from pathlib import Path

from .db import connect

STATUS_LABELS = {"answered": "답변함", "no_evidence": "근거 없음", "stopped": "위험 신호로 중단",
                 "rejected": "검증 실패로 숨김", "blocked": "금기 성분 권유로 막음",
                 "fallback": "근거 없음 · 관심사 기준 일반 추천"}


def status():
    """rag DB가 어느 규칙 버전에서 복제됐는지와 dev DB의 현재 버전을 나란히 보여 준다."""
    dev, rag = connect("dev"), connect("rag")
    try:
        current = dev.read("MATCH (r:RuleSet) RETURN r.version")
        meta = rag.read("MATCH (m:RagMeta {id: 'current'}) RETURN m.source_rule_version, toString(m.cloned_at)")
    finally:
        dev.close()
        rag.close()
    current_version = current[0][0] if current else None
    cloned_version, cloned_at = meta[0] if meta else (None, None)
    print(f"개발 DB 규칙 버전 : {current_version}")
    print(f"복제본 규칙 버전  : {cloned_version} (복제 시각 {cloned_at})")
    if cloned_version != current_version:
        print("버전이 다릅니다. python -m wcrag.index.clone 으로 다시 복제하세요.")
    else:
        print("버전이 같습니다.")


def render(result: dict) -> str:
    """ask()의 결과를 사람이 읽는 글로 바꾼다."""
    usage = result["usage"]
    lines = [f"[상태] {result['status']} ({STATUS_LABELS.get(result['status'], '')}) · 검색 방식 {result['mode']}",
             "", result["text"]]
    names = result.get("ingredient_names", {})
    if result["recommended"] or result["cautioned"]:
        lines += ["", f"[권함] {', '.join(names.get(i, i) for i in result['recommended']) or '없음'} · "
                      f"[주의] {', '.join(names.get(i, i) for i in result['cautioned']) or '없음'}"]
    if result["warnings"]:
        lines += ["", "[경고]"] + [f"- {w}" for w in result["warnings"]]
    if result["evidence"]:
        lines += ["", "[인용한 근거]"] + [f"- {e['id']} | {' '.join(e['text'].split())}" for e in result["evidence"]]
    checked = "판정함" if result["safety_checked"] else "판정 안 함"
    lines += ["", f"[금기 판정] {checked} · [사용량] 입력 {usage['tokens_in']}토큰, 출력 {usage['tokens_out']}토큰, "
                  f"{usage['latency_s']}초"]
    return "\n".join(lines)


def default_ask(question: str, patient: dict | None, mode: str) -> dict:
    """실제 실행. rag DB에 붙고 OpenAI를 호출한다(과금)."""
    from . import llm
    from .answer.ask import ask
    graph = connect("rag")
    try:
        return ask(graph, question, patient, mode, embed=llm.embed, complete=llm.complete)
    finally:
        graph.close()


def main(argv=None, ask_fn=default_ask):
    parser = argparse.ArgumentParser(prog="wcrag")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="복제본과 개발 DB의 규칙 버전 비교")
    asking = sub.add_parser("ask", help="질문에 그래프 근거로 답한다 (OpenAI 호출)")
    asking.add_argument("question")
    asking.add_argument("--patient", help="환자 정보 JSON 파일. 있으면 금기를 판정한다")
    asking.add_argument("--mode", default="auto", choices=["auto", "vector", "local"])
    args = parser.parse_args(argv)
    if args.command == "status":
        status()
    elif args.command == "ask":
        patient = json.loads(Path(args.patient).read_text(encoding="utf-8")) if args.patient else None
        print(render(ask_fn(args.question, patient, args.mode)))


if __name__ == "__main__":
    main()
