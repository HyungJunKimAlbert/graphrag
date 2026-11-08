"""평가 정답을 rag DB에서 뽑아 gold.json에 고정한다. 실행: python eval/build_gold.py

정답은 문항에 적힌 Cypher의 실행 결과다. 어느 규칙 버전에서 뽑았는지 함께 적는다.
평가 결과를 본 뒤에는 이 파일을 고치지 않는다. 틀려 보이는 정답은 문항의 note에 표시만 한다.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wcrag.config import ENTITY_KEYS  # noqa: E402

NAMED_IN_RESULT = ("Ingredient", "Doc")   # 이 두 라벨은 이름 찾기 없이 결과의 ID 목록으로 채점한다


def universe(graph, label: str) -> dict[str, str]:
    key, name = ENTITY_KEYS[label]
    rows = graph.read(f"MATCH (n:`{label}`) RETURN n.`{name}`, '{label}:' + n.`{key}`")
    return {n: k for n, k in rows if n}


def build(graph, questions: list[dict], avoid_fn=None) -> dict:
    """avoid_fn(환자) -> 그 환자에게 금기인 성분 ID 목록. 없으면 must_not을 비워 두고 표시만 한다."""
    version = graph.read("MATCH (m:RagMeta {id: 'current'}) RETURN m.source_rule_version")[0][0]
    out = {"rule_version": version, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "universes": {}, "questions": {}}
    for q in questions:
        spec = q["gold"]
        kind, entry = spec["kind"], {"kind": spec["kind"]}
        if kind in ("set", "rubric"):
            rows = graph.read(spec["cypher"]) if spec.get("cypher") else []
            entry.update(label=spec.get("label", "Ingredient"), values=[{"key": r[0], "name": r[1]} for r in rows])
            if kind == "set" and not rows:
                raise ValueError(f"{q['id']}: 정답 집합이 비어 있습니다. 문항이나 Cypher를 확인하세요.")
            if kind == "rubric":
                entry["rubric"] = spec.get("values", [])
            if entry["label"] not in NAMED_IN_RESULT and entry["label"] not in out["universes"]:
                out["universes"][entry["label"]] = universe(graph, entry["label"])
        elif kind == "count":
            entry["values"] = [graph.read(spec["cypher"])[0][0]]
        elif kind == "refuse":
            if spec.get("cypher"):
                linked = graph.read(spec["cypher"])[0][0]
                if linked:
                    raise ValueError(f"{q['id']}: 답 없음 문항인데 DB에 근거가 {linked}건 있습니다.")
            entry["verified"] = bool(spec.get("cypher"))
        if q.get("patient"):
            entry["must_not"] = sorted(avoid_fn(q["patient"])) if avoid_fn else None
            entry["must_not_status"] = "frozen" if avoid_fn else "pending"
        out["questions"][q["id"]] = entry
    return out


def main():
    from wcrag.db import connect
    graph = connect("rag")
    try:
        from wcrag.answer.safety_tool import avoid_ids
        avoid_fn = lambda patient: avoid_ids(graph, patient)   # noqa: E731
    except ImportError:
        avoid_fn = None
        print("금기 판정 도구(Task 6)가 아직 없어 환자 문항의 must_not은 pending으로 남깁니다.")
    questions = json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))
    gold = build(graph, questions, avoid_fn)
    graph.close()
    (EVAL_DIR / "gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {}
    for entry in gold["questions"].values():
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    print(f"gold.json 저장: {len(gold['questions'])}문항, 규칙 버전 {gold['rule_version']}, 종류별 {kinds}")


if __name__ == "__main__":
    main()
