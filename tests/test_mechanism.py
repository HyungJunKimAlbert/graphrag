import pytest

from rxrag import candidates, config, graph, mechanism
from rxrag.graph import ConfigError, ReadOnlyDB, WriteRefused

ING = [["IND-2023-19", "엘씨27 복합물"], ["NUT-b12", "비타민 B12"], ["NTC-1-1", "홍경천 추출물"]]


def test_ingredient_resolved_by_approval_number_then_name():
    ids = mechanism.ingredient_index(ING)
    assert mechanism.resolve_ingredient("L. plantarum LC27과 B. longum LC67(NVP-1702)(제2023-19호)", ids) == "IND-2023-19"
    assert mechanism.resolve_ingredient("비타민B12", ids) == "NUT-b12"
    assert mechanism.resolve_ingredient("홍경천 추출물(기능성원료인정제2009-9호)", ids) == "NTC-1-1"  # 번호가 그래프에 없으면 이름
    assert mechanism.resolve_ingredient("없는 원료", ids) is None


@pytest.mark.graph
def test_symptom_resolved_with_engine_aliases():
    dev = graph.connect()
    assert mechanism.resolve_symptom(dev, "불면")[1] == "불면증"
    assert mechanism.resolve_symptom(dev, "붓기")[1] == "부종"
    assert mechanism.resolve_symptom(dev, "없는증상") == (None, None)


def rag_row(verdict="근거 있음", iid="NUT-b12"):
    return ["SYM-fatigue-chronic", "만성피로", "M40", "미량영양소 결핍(비타민B12)", "질병관리청", verdict, iid,
            "에너지 이용에 필요", "식약처 고시", "https://health.kdca.go.kr/x"]


class FakeRag:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def run(self, query, **params):
        self.queries.append((query, params))
        return [r for r in self.rows if r[0] in params["ids"] and r[5] in params["verdicts"]]


class FakeDev:
    """recommendable 성분만 이름·유형과 함께 돌려준다."""

    def __init__(self, recommendable):
        self.recommendable = recommendable

    def run(self, query, ids, sex=None, pregnant=None):
        self.params = {"sex": sex, "pregnant": pregnant}
        return [[i, *self.recommendable[i]] for i in ids if i in self.recommendable]


def test_mechanism_paths_only_strong_evidence_and_recommendable():
    rag = FakeRag([rag_row(), rag_row("부분 근거", "NTC-1-1"), rag_row(iid="IND-off")])
    dev = FakeDev({"NUT-b12": ("비타민 B12", "nutrient"), "NTC-1-1": ("홍경천 추출물", "notice")})
    rows = candidates.mechanism_paths(dev, rag, ["SYM-fatigue-chronic"], "F", False)
    assert dev.params == {"sex": "F", "pregnant": False}  # 성별·임신 대상 기능성 조건을 dev 쿼리로 넘긴다
    assert [r[0] for _, r in rows] == ["NUT-b12"]  # 부분 근거·추천 불가 성분은 빠진다
    kind, row = rows[0]
    f = candidates.fact(kind, row)
    assert f["fact_id"] == "SYM_MECH:SYM-fatigue-chronic>M40>NUT-b12" and f["label"] == "증상 기전 근거"
    assert "만성피로 → 미량영양소 결핍(비타민B12)" in f["text"] and "질병관리청" in f["text"]
    assert f["source"] == "https://health.kdca.go.kr/x"


def test_mechanism_paths_skip_without_symptoms_or_rag():
    assert candidates.mechanism_paths(FakeDev({}), FakeRag([rag_row()]), [], "F", False) == []
    assert candidates.mechanism_paths(FakeDev({}), None, ["SYM-fatigue-chronic"], "F", False) == []


def test_mechanism_tier_between_depletion_and_category():
    assert candidates.KINDS["DRUG_SYM_DEPLETE"] < candidates.KINDS["SYM_MECH"] < candidates.KINDS["CAT_FUNC"]


def test_rag_connection_only_allowed_port(monkeypatch):
    monkeypatch.setattr(graph, "_rag_env", lambda: {"NEO4J_HTTP_URL": "http://localhost:7475", "NEO4J_PASSWORD": "x"})
    with pytest.raises(ConfigError):
        graph.connect_rag()


def test_rag_read_connection_refuses_writes(monkeypatch):
    monkeypatch.setattr(graph, "_rag_env", lambda: {"NEO4J_HTTP_URL": f"http://localhost:{config.RAG_HTTP_PORT}",
                                                     "NEO4J_PASSWORD": "x"})
    db = graph.connect_rag()
    assert isinstance(db, ReadOnlyDB)
    with pytest.raises(WriteRefused):
        db.run("CREATE (n)")
    assert not isinstance(graph.connect_rag(write=True), ReadOnlyDB)


@pytest.mark.graph
def test_loaded_mechanism_layer_reachable():
    rag = graph.connect_rag()
    assert rag.run("MATCH (m:Mechanism) RETURN count(m)")[0][0] >= 50
    assert rag.run("MATCH ()-[e:HAS_MECHANISM {verdict: '근거 있음'}]->() RETURN count(e)")[0][0] > 0


@pytest.mark.graph
def test_opposite_sex_only_ingredient_dropped():
    """쏘팔메토 복합물은 기능성이 남성 대상뿐이다. 여성 환자의 기전 경로에서는 빠진다."""
    dev, rag = graph.connect(), graph.rag()
    sid = mechanism.resolve_symptom(dev, "탈모")[0]  # 탈모 → 남성호르몬(안드로겐) 작용 → 쏘팔메토
    names = lambda sex: {r[1] for _, r in candidates.mechanism_paths(dev, rag, [sid], sex, False)}
    assert any("쏘팔메토" in n for n in names("M"))
    assert not any("쏘팔메토" in n for n in names("F"))
