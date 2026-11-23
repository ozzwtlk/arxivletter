# Adapted from AXIOM (../../Axiom/backend/axiom/model_adapters).
"""JSON Schema → GBNF grammar for llama.cpp, with LaTeX-tolerant strings.

Why not llama.cpp's built-in `response_format: json_schema`: its JSON string rule only allows the standard escapes
after a backslash (" \\ / b f n r t u). Models often write LaTeX with a single backslash ("\\lambda"); under that
grammar `\\l` is impossible, so sampling is forced into an allowed escape and the model continues from there:
`\\lambda_0` became `\\frac{\\tilde{\\nu}...}`, `\\phi` became `\\theta`, `\\gamma` became `\\bar{\\nu}` -- silent
math corruption in otherwise valid JSON (found by the faithfulness checker on Carroll ch. 2).

This grammar accepts a backslash followed by ANY printable character inside strings. (AXIOM's v1 allowed only
letters, which made `61\\%`, `\\,`, `\\;` or `\\{` impossible: the model was forced into a letter command and
`\\%` came out as `\\pi` — found here on a real paper.) Such output is not
strict JSON, but `llm.parse_json` already repairs it (`repair_latex_escapes`) before decoding, so the LaTeX survives.

Only the schema subset arXivletter uses is supported (object with properties/required, array with items/minItems/maxItems,
string, integer, number, boolean, null, type lists, enums of strings/numbers). Anything else raises
`UnsupportedSchema`, and the caller falls back to llama.cpp's own json_schema handling.
"""

from __future__ import annotations

import json
import re


class UnsupportedSchema(ValueError):
    pass


_KNOWN = {"type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "enum",
          "description", "title"}

BASE = r'''ws ::= [ \t\n]{0,20}
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" [^\x00-\x1F\x7F]
integer ::= "-"? ( "0" | [1-9] [0-9]{0,15} )
number ::= integer ( "." [0-9]{1,16} )? ( [eE] [-+]? [0-9]{1,4} )?
boolean ::= "true" | "false"
null ::= "null"
'''


def _lit(s: str) -> str:
    """A GBNF string literal that matches `s` exactly."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


class _Builder:
    def __init__(self) -> None:
        self.rules: dict[str, str] = {}
        self._n = 0

    def _new(self, hint: str, body: str) -> str:
        hint = re.sub(r"[^a-zA-Z0-9-]", "-", hint)[:40]
        self._n += 1
        name = f"{hint}-{self._n}"
        self.rules[name] = body
        return name

    def visit(self, s: dict, hint: str = "v") -> str:
        extra = set(s) - _KNOWN
        if extra:
            raise UnsupportedSchema(f"unsupported keywords {sorted(extra)}")
        if "enum" in s:
            return self._new(hint, " | ".join(_lit(json.dumps(v, ensure_ascii=False)) for v in s["enum"]))
        t = s.get("type")
        if isinstance(t, list):
            return self._new(hint, " | ".join(self.visit(dict(s, type=x), hint) for x in t))
        if t in ("string", "integer", "number", "boolean", "null"):
            return t
        if t == "array":
            item = self.visit(s.get("items", {"type": "string"}), hint + "-item")
            lo, hi = int(s.get("minItems", 0)), s.get("maxItems")
            if hi is not None and int(hi) == 0:
                return self._new(hint, '"[" ws "]"')
            rest_lo = max(lo - 1, 0)
            rest = f'( "," ws {item} ws ){{{rest_lo},{"" if hi is None else int(hi) - 1}}}'
            body = f'"[" ws {item} ws {rest} "]"'
            return self._new(hint, body if lo >= 1 else f'"[" ws "]" | {body}')
        if t == "object":
            if s.get("additionalProperties", False) not in (False,):
                raise UnsupportedSchema("additionalProperties must be false")
            props = s.get("properties", {})
            req = [k for k in props if k in set(s.get("required", []))]
            opt = [k for k in props if k not in req]
            if not req:
                raise UnsupportedSchema("object without required properties")
            kv = lambda k: f'{_lit(json.dumps(k))} ws ":" ws {self.visit(props[k], hint + "-" + re.sub(r"[^a-zA-Z0-9]", "-", k))}'
            parts = [kv(k) for k in req] + [f'( "," ws {kv(k)} ws )?' for k in opt]
            body = '"{" ws ' + ' ws "," ws '.join(parts[:len(req)]) + " ws " + " ".join(parts[len(req):]) + ' "}"'
            return self._new(hint, body)
        raise UnsupportedSchema(f"unsupported type {t!r}")


DECODING = "gbnf-latex-v2"  # v2: any printable character after a backslash (\\%, \\, \\; \\{ ...)  # part of LLM cache keys for schema-constrained requests

# With thinking on, the chat template opens <think> in the prompt, so the grammar must first allow the reasoning
# text and its closing tag (llama.cpp's own json_schema path does the same internally).
THOUGHT = 'thought ::= ( [^<] | "<" [^/] | "</" [^t] )*\n'


def schema_to_gbnf(schema: dict, reasoning: bool = False) -> str:
    b = _Builder()
    top = b.visit(schema, "root")
    root = f'root ::= thought "</think>" ws {top} ws\n' if reasoning else f"root ::= ws {top} ws\n"
    return BASE + root + "".join(f"{k} ::= {v}\n" for k, v in b.rules.items()) + (THOUGHT if reasoning else "")
