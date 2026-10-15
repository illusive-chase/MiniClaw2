from __future__ import annotations

import sys
import textwrap
import unittest
from typing import Any

from miniclaw2 import code_analysis as ca


def decode(result: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """Expand the relative token encoding into readable rows."""
    lines = text.split("\n")
    refs = dict(zip(result["refs"][0::2], result["refs"][1::2]))
    tokens = result["tokens"]
    rows = []
    line, col = 1, 0
    for index in range(0, len(tokens), 5):
        delta_line, delta_col, length, kind, mods = tokens[index:index + 5]
        if delta_line:
            line += delta_line
            col = delta_col
        else:
            col += delta_col
        utf16 = lines[line - 1].encode("utf-16-le")
        word = utf16[col * 2:(col + length) * 2].decode("utf-16-le")
        position = index // 5
        rows.append({
            "line": line, "col": col, "text": word, "type": kind, "mods": mods,
            "symbol": result["symbols"][refs[position]] if position in refs else None,
        })
    return rows


def analyze(files: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    texts = {path: ca.normalize_newlines(textwrap.dedent(src).lstrip("\n")) for path, src in files.items()}
    results = ca.analyze_project(texts)
    return {path: decode(result, texts[path]) for path, result in results.items()}


def find(rows: list[dict[str, Any]], text: str, line: int | None = None, nth: int = 0) -> dict[str, Any]:
    hits = [r for r in rows if r["text"] == text and (line is None or r["line"] == line)]
    if len(hits) <= nth:
        raise AssertionError(f"no token {text!r} (line {line}) in {[r['text'] for r in rows]}")
    return hits[nth]


def target(row: dict[str, Any]) -> tuple[str, int] | None:
    symbol = row["symbol"]
    if symbol is None or symbol["target"] is None:
        return None
    return symbol["target"]["path"], symbol["target"]["line"]


class CoordinateTest(unittest.TestCase):
    def test_utf16_columns_with_cjk_emoji_and_tabs(self) -> None:
        src = 's = "中文😀"; value = 1\nif s:\n\tother = value\n'
        rows = analyze({"m.py": src})["m.py"]
        value = find(rows, "value", line=1)
        # 's = "' is 5, 中文 is 2 units, 😀 is a surrogate pair, '"; ' is 3.
        self.assertEqual(value["col"], 5 + 2 + 2 + 3)
        self.assertEqual(find(rows, "other")["col"], 1)
        self.assertEqual(target(find(rows, "value", line=3)), ("m.py", 1))

    def test_line_index_byte_conversion(self) -> None:
        index = ca.LineIndex("x = 'é'; y\n")
        self.assertEqual(index.byte_to_cp(1, len("x = 'é'; ".encode())), len("x = 'é'; "))


class FStringTest(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 12), "f-string tokens are 3.12+")
    def test_doubled_braces_do_not_shift_columns(self) -> None:
        src = 'name = 1\nmsg = f"a{{b}} {name!r} tail"\n'
        rows = analyze({"m.py": src})["m.py"]
        name = find(rows, "name", line=2)
        self.assertEqual(name["col"], src.split("\n")[1].index("{name") + 1)
        self.assertEqual(target(name), ("m.py", 1))
        strings = [r for r in rows if r["line"] == 2 and r["type"] == ca.T_STRING]
        self.assertEqual("".join(r["text"] for r in strings), 'f"a{{b}} ' + ' tail"')

    def test_triple_quoted_strings_split_per_line(self) -> None:
        rows = analyze({"m.py": 'x = """a\nb\n"""\n'})["m.py"]
        lines = sorted({r["line"] for r in rows if r["type"] == ca.T_STRING})
        self.assertEqual(lines, [1, 2, 3])


class LexicalTest(unittest.TestCase):
    def test_soft_keywords_only_in_keyword_position(self) -> None:
        src = """
        match = 1
        match match:
            case 1:
                pass
            case _:
                pass
        type X = int
        """
        rows = analyze({"m.py": src})["m.py"]
        self.assertEqual(find(rows, "match", line=1)["type"], ca.T_VARIABLE)
        self.assertEqual(find(rows, "match", line=2, nth=0)["type"], ca.T_KEYWORD)
        self.assertEqual(find(rows, "match", line=2, nth=1)["type"], ca.T_VARIABLE)
        self.assertEqual(find(rows, "case", line=3)["type"], ca.T_KEYWORD)
        self.assertEqual(find(rows, "_", line=5)["type"], ca.T_KEYWORD)
        if sys.version_info >= (3, 12):
            self.assertEqual(find(rows, "type", line=7)["type"], ca.T_KEYWORD)

    def test_syntax_error_keeps_lexical_colour_and_no_refs(self) -> None:
        text = "def f(:\n    return 'x'  # note\n"
        result = ca.analyze_project({"m.py": text})["m.py"]
        self.assertEqual(result["syntax_error"]["line"], 1)
        rows = decode(result, text)
        self.assertEqual(find(rows, "def")["type"], ca.T_KEYWORD)
        self.assertTrue(any(r["type"] == ca.T_STRING for r in rows))
        self.assertTrue(any(r["type"] == ca.T_COMMENT for r in rows))
        self.assertEqual(result["refs"], [])

    def test_unterminated_input_colours_the_prefix(self) -> None:
        text = "x = 1\ny = (\n"
        result = ca.analyze_project({"m.py": text})["m.py"]
        rows = decode(result, text)
        self.assertEqual(find(rows, "1")["type"], ca.T_NUMBER)


class ScopeTest(unittest.TestCase):
    def test_class_scope_is_invisible_to_methods(self) -> None:
        src = """
        x = 1
        class C:
            x = 2
            def m(self):
                return x
        """
        rows = analyze({"m.py": src})["m.py"]
        self.assertEqual(target(find(rows, "x", line=5)), ("m.py", 1))

    def test_global_nonlocal_and_comprehensions(self) -> None:
        src = """
        counter = 0
        def bump():
            global counter
            counter = counter + 1
        def outer():
            total = 0
            def inner():
                nonlocal total
                total += 1
            return [total for total in range(3)]
        """
        rows = analyze({"m.py": src})["m.py"]
        # The right-hand side reads the value from before this rebinding.
        self.assertEqual(target(find(rows, "counter", line=4, nth=1)), ("m.py", 1))
        self.assertEqual(target(find(rows, "counter", line=3)), ("m.py", 1))
        self.assertEqual(target(find(rows, "total", line=9)), ("m.py", 6))
        comp = [r for r in rows if r["line"] == 10 and r["text"] == "total"]
        self.assertEqual(target(comp[0]), ("m.py", 10))

    def test_latest_binding_before_use(self) -> None:
        src = """
        value = 1
        value = 2
        print(value)
        """
        rows = analyze({"m.py": src})["m.py"]
        self.assertEqual(target(find(rows, "value", line=3)), ("m.py", 2))


class ImportTest(unittest.TestCase):
    def test_reexport_chain_reaches_the_definition(self) -> None:
        rows = analyze({
            "pkg/__init__.py": "from .core import Engine\n",
            "pkg/core.py": "class Engine:\n    pass\n",
            "app.py": "from pkg import Engine\nimport pkg.core\nEngine()\npkg.core.Engine\n",
        })["app.py"]
        self.assertEqual(target(find(rows, "Engine", line=1)), ("pkg/core.py", 1))
        self.assertEqual(target(find(rows, "Engine", line=3)), ("pkg/core.py", 1))
        self.assertEqual(target(find(rows, "pkg", line=2)), ("pkg/__init__.py", 1))
        self.assertEqual(target(find(rows, "core", line=2)), ("pkg/core.py", 1))
        self.assertEqual(target(find(rows, "Engine", line=4)), ("pkg/core.py", 1))

    def test_import_cycle_terminates(self) -> None:
        rows = analyze({
            "a.py": "from b import thing\n",
            "b.py": "from a import thing\n",
            "c.py": "from a import thing\nthing\n",
        })["c.py"]
        self.assertFalse([r for r in rows if r["line"] == 2 and r["symbol"]])

    def test_relative_and_star_imports(self) -> None:
        rows = analyze({
            "pkg/__init__.py": "",
            "pkg/util.py": "__all__ = ['helper']\ndef helper():\n    pass\ndef hidden():\n    pass\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/mod.py": "from ..util import *\nfrom .. import util\nhelper()\nhidden()\nutil.helper()\n",
        })["pkg/sub/mod.py"]
        self.assertEqual(target(find(rows, "helper", line=3)), ("pkg/util.py", 2))
        self.assertIsNone(find(rows, "hidden")["symbol"])
        self.assertEqual(target(find(rows, "util", line=2)), ("pkg/util.py", 1))
        self.assertEqual(target(find(rows, "helper", line=5)), ("pkg/util.py", 2))

    def test_stdlib_and_third_party_are_external(self) -> None:
        rows = analyze({"m.py": "import os\nfrom fastapi import HTTPException\nos.path.join\nraise HTTPException(404)\n"})["m.py"]
        os_row = find(rows, "os", line=3)
        self.assertEqual(os_row["type"], ca.T_MODULE)
        self.assertTrue(os_row["mods"] & ca.M_EXTERNAL)
        self.assertIsNone(os_row["symbol"]["target"])
        exc = find(rows, "HTTPException", line=4)
        self.assertEqual(exc["type"], ca.T_CLASS)
        self.assertEqual(exc["symbol"]["signature"], "(class) fastapi.HTTPException")


class InferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = analyze({
            "models.py": """
            from dataclasses import dataclass
            from typing import Optional


            @dataclass
            class Point:
                x: int
                y: int = 0

                def norm(self) -> float:
                    return 0.0


            class Store:
                def __init__(self, point: Point) -> None:
                    self.point = point
                    self.points: list[Point] = []
                    self.by_name: dict[str, Point] = {}

                @property
                def first(self) -> Point:
                    return self.points[0]

                def maybe(self) -> Optional[Point]:
                    return None

                def __enter__(self) -> "Store":
                    return self

                def __exit__(self, *exc) -> None:
                    pass


            class Error(Exception):
                code: int = 0
            """,
            "use.py": """
            from models import Point, Store, Error


            def run(store: Store, other) -> None:
                store.point.norm()
                store.maybe().norm()
                for index, p in enumerate(store.points):
                    p.norm()
                for name, p2 in store.by_name.items():
                    p2.norm()
                with Store(Point(x=1)) as opened:
                    opened.first.norm()
                try:
                    pass
                except Error as err:
                    err.code
                    err.args
                if isinstance(other, Point):
                    other.norm()
                other.norm()
                Point(x=1, y=2)
            """,
        })["use.py"]

    def test_attribute_from_parameter_assignment(self) -> None:
        self.assertEqual(target(find(self.rows, "norm", line=5)), ("models.py", 10))

    def test_optional_return_narrows(self) -> None:
        self.assertEqual(target(find(self.rows, "norm", line=6)), ("models.py", 10))

    def test_for_unpacking_enumerate_and_items(self) -> None:
        self.assertEqual(target(find(self.rows, "norm", line=8)), ("models.py", 10))
        self.assertEqual(target(find(self.rows, "norm", line=10)), ("models.py", 10))

    def test_with_as_and_property(self) -> None:
        self.assertEqual(target(find(self.rows, "first", line=12)), ("models.py", 21))
        self.assertEqual(find(self.rows, "first", line=12)["type"], ca.T_PROPERTY)
        self.assertEqual(target(find(self.rows, "norm", line=12)), ("models.py", 10))

    def test_except_as_and_external_base(self) -> None:
        self.assertEqual(target(find(self.rows, "code", line=16)), ("models.py", 35))
        args = find(self.rows, "args", line=17)
        self.assertIsNotNone(args["symbol"])
        self.assertIsNone(args["symbol"]["target"])

    def test_isinstance_narrowing_and_no_guessing(self) -> None:
        self.assertEqual(target(find(self.rows, "norm", line=19)), ("models.py", 10))
        unknown = find(self.rows, "norm", line=20)
        # Unknown receiver: call-position colour, but no hover and no link.
        self.assertEqual(unknown["type"], ca.T_FUNCTION)
        self.assertIsNone(unknown["symbol"])

    def test_dataclass_keywords_link_to_fields(self) -> None:
        self.assertEqual(target(find(self.rows, "x", line=21)), ("models.py", 7))
        self.assertEqual(target(find(self.rows, "y", line=21)), ("models.py", 8))
        point = find(self.rows, "Point", line=21)
        self.assertEqual(point["symbol"]["signature"], "(class) Point(x: int, y: int = 0)")


class ClassModelTest(unittest.TestCase):
    def test_mro_super_and_external_members(self) -> None:
        rows = analyze({"m.py": """
            import unittest


            class Base:
                def hello(self) -> str:
                    return "hi"


            class Child(Base):
                def hello(self) -> str:
                    return super().hello()


            class Case(unittest.TestCase):
                def test(self) -> None:
                    self.assertEqual(1, 1)
                    Child().hello().upper()
            """})["m.py"]
        self.assertEqual(target(find(rows, "hello", line=11)), ("m.py", 5))
        external = find(rows, "assertEqual")
        self.assertIsNone(external["symbol"]["target"])
        self.assertEqual(external["symbol"]["qualname"], "unittest.TestCase.assertEqual")
        upper = find(rows, "upper")
        self.assertEqual(upper["symbol"]["origin"], "builtin")
        self.assertIn("str", upper["symbol"]["qualname"])

    def test_union_with_two_definitions_is_unknown(self) -> None:
        rows = analyze({"m.py": """
            class A:
                def run(self) -> None:
                    pass


            class B:
                def run(self) -> None:
                    pass


            def go(x: A | B) -> None:
                x.run()
            """})["m.py"]
        run = find(rows, "run", line=12)
        self.assertIsNone(run["symbol"])

    def test_union_resolving_to_one_definition_links(self) -> None:
        rows = analyze({"m.py": """
            class A:
                def run(self) -> None:
                    pass


            class B(A):
                pass


            def go(x: A | B) -> None:
                x.run()
            """})["m.py"]
        self.assertEqual(target(find(rows, "run", line=11)), ("m.py", 2))

    def test_pydantic_signature_and_enum_members(self) -> None:
        rows = analyze({"m.py": """
            from enum import Enum
            from pydantic import BaseModel


            class Color(Enum):
                RED = 1

                def describe(self) -> str:
                    return ""


            class Model(BaseModel):
                name: str
                _private: int = 0


            Color.RED.describe()
            Model(name="x")
            """})["m.py"]
        self.assertEqual(target(find(rows, "describe", line=17)), ("m.py", 8))
        self.assertEqual(find(rows, "Model", line=18)["symbol"]["signature"], "(class) Model(name: str)")
        self.assertEqual(target(find(rows, "name", line=18)), ("m.py", 13))

    def test_hover_signatures_and_docs(self) -> None:
        rows = analyze({"m.py": '''
            def greet(name: str, *, loud: bool = False) -> str:
                """Say hello."""
                return name


            async def fetch() -> int:
                return 1


            greet("a")
            len("a")
            '''})["m.py"]
        greet = find(rows, "greet", line=10)["symbol"]
        self.assertEqual(greet["signature"], "(function) greet(name: str, *, loud: bool = False) -> str")
        self.assertEqual(greet["doc"], "Say hello.")
        fetch = find(rows, "fetch", line=6)
        self.assertTrue(fetch["mods"] & ca.M_ASYNC)
        self.assertTrue(fetch["mods"] & ca.M_DECLARATION)
        self.assertIn("async fetch()", fetch["symbol"]["signature"])
        builtin = find(rows, "len")
        self.assertTrue(builtin["mods"] & ca.M_BUILTIN)
        self.assertIsNotNone(builtin["symbol"]["doc"])


class ProjectRegressionTest(unittest.TestCase):
    """The analyser on this repository itself, with loose floors."""

    @classmethod
    def setUpClass(cls) -> None:
        from pathlib import Path

        from miniclaw2.code_files import list_python_files

        root = Path(__file__).resolve().parents[2]
        texts = {}
        for path in list_python_files(root):
            raw = (root / path).read_bytes()
            if len(raw) <= ca.ANALYSIS_CAP:
                texts[path] = ca.decode_source(raw, python=True)
        cls.index = ca.ProjectIndex(texts)
        cls.root = root

    def test_resolution_rates(self) -> None:
        counts = {"project": 0, "external": 0, "unknown": 0}
        for path in sorted(self.index.texts):
            if not path.startswith("backend/miniclaw2/"):
                continue
            model = self.index.model(path)
            assert model is not None
            for node, scope in model.attrs:
                _value, ref = self.index.member(self.index.infer(model, node.value, scope), node.attr)
                if ref is None:
                    counts["unknown"] += 1
                elif ref.origin in ("project", "module"):
                    counts["project"] += 1
                else:
                    counts["external"] += 1
        total = sum(counts.values())
        self.assertGreaterEqual(counts["project"] / total, 0.45, counts)
        self.assertGreaterEqual(counts["external"] / total, 0.20, counts)

    def test_known_jump(self) -> None:
        path = "backend/miniclaw2/app.py"
        rows = decode(ca.analyze_file(self.index, path), self.index.texts[path])
        jumps = [target(r) for r in rows if r["text"] == "get_project" and r["symbol"]]
        self.assertTrue(jumps)
        defined = self.index.texts["backend/miniclaw2/registry.py"].split("\n")
        for jump in jumps:
            self.assertIsNotNone(jump)
            assert jump is not None
            self.assertEqual(jump[0], "backend/miniclaw2/registry.py")
            self.assertIn("def get_project", defined[jump[1] - 1])


if __name__ == "__main__":
    unittest.main()
