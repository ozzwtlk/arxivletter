"""Evidence checks, JSON/LaTeX repair and the llama.cpp grammar."""


from arxivletter.analysis.grounding import Grounder
from arxivletter.model_adapters.gbnf import schema_to_gbnf
from arxivletter.orchestration.llm import parse_json


def test_quotes_exact_moved_fuzzy_missing(sample_doc):
    g = Grounder(sample_doc)
    caveat = next(b for s in sample_doc["sections"] for b in s["blocks"] if "back-reaction" in b.get("text", ""))
    v = g.verify_statement({"quote": "which we leave for future work", "ref": caveat["id"], "kind": "future_work"})
    assert v and v["match"] == "exact" and v["section_title"] == "Caveats"
    v = g.verify_statement({"quote": "which we leave for future work", "ref": "P1"})
    assert v and v["match"] == "moved" and v["ref"] == caveat["id"]
    v = g.verify_statement({"quote": "It remains an open questin whether the rate survives in inhomogenous fields", "ref": "P1"})
    assert v and v["match"] == "fuzzy" and "open question" in v["quote"]
    assert g.verify_statement({"quote": "We prove the Riemann hypothesis along the way.", "ref": "P1"}) is None
    assert g.stats["dropped_unverified"] == 1


def test_ids_cites_numbers(sample_doc):
    g = Grounder(sample_doc)
    assert g.refs(["P1", "[E1]", "P999", "S2"]) == ["P1", "E1", "S2"]
    assert g.equation("E1")["number"] == "1" and g.equation("P1") is None
    assert g.cites(["Gies:2005", "Made:2099up"]) == ["Gies:2005"]
    assert g.unsupported_numbers("a 10% effect at 2 GeV with 37.5 events") == ["37.5"]


def test_parse_json_latex_repairs():
    raw = r'{"a": "\frac{1}{2} \nabla \tau 61\% \, \upsilon \underline{x} \u00e9 \rightarrow"}'
    assert parse_json(raw)["a"] == "\\frac{1}{2} \\nabla \\tau 61\\% \\, \\upsilon \\underline{x} é \\rightarrow"


def test_gbnf_allows_any_escaped_printable():
    g = schema_to_gbnf({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    char_rule = next(line for line in g.splitlines() if line.startswith("char ::="))
    assert '"\\\\" [^\\x00-\\x1F\\x7F]' in char_rule  # \%, \, \; \{ are possible (AXIOM v1 forced them to letters)


def test_signposting_is_not_an_open_problem():
    from arxivletter.analysis.digest import is_signposting
    assert is_signposting("We will investigate the effects of the non-linearity in the next section.")
    assert is_signposting("This is discussed in Sec. 4.")
    assert not is_signposting("We leave the inclusion of back-reaction for future work.")
    assert not is_signposting("It remains an open question whether the rate survives.")
