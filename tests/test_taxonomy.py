import pytest

from rxrag import graph, taxonomy


class FakeDB:
    def __init__(self, names):
        self.names = names

    def run(self, query, **params):
        return [[n] for n in self.names]


def test_load_14():
    cats = taxonomy.load()
    assert len(cats) == 14
    assert next(c for c in cats if c["category"] == "스트레스/수면")["keywords"][:2] == ["불면", "스트레스"]


def test_keywords_have_no_blanks():
    assert all(k and k == k.strip() for c in taxonomy.load() for k in c["keywords"])


def test_mismatch_reports_both_sides():
    cats = [{"category": "A", "definition": "", "keywords": []}, {"category": "B", "definition": "", "keywords": []}]
    assert taxonomy.graph_mismatch(FakeDB(["B", "C"]), cats) == {"only_xlsx": ["A"], "only_graph": ["C"]}


def test_prompt_text_line_format():
    text = taxonomy.prompt_text([{"category": "혈당", "definition": "정의", "keywords": ["a", "b"]}])
    assert text == "- 혈당: 정의 / 키워드: a, b"


@pytest.mark.graph
def test_matches_graph():
    assert taxonomy.graph_mismatch(graph.connect(), taxonomy.load()) == {"only_xlsx": [], "only_graph": []}


def test_taxonomy_path_not_hardcoded_to_user(monkeypatch, tmp_path):
    import importlib
    from rxrag import config
    monkeypatch.setenv("RXRAG_TAXONOMY_XLSX", str(tmp_path / "t.xlsx"))
    try:
        assert importlib.reload(config).TAXONOMY_XLSX == tmp_path / "t.xlsx"
    finally:
        monkeypatch.delenv("RXRAG_TAXONOMY_XLSX")
        importlib.reload(config)
    assert "/Users/user/" not in open(config.__file__, encoding="utf-8").read()
