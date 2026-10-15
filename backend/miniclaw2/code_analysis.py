"""Offline Python analysis for the code-reading page.

Run by ``code_index`` as ``sys.executable -I code_analysis.py`` with the
cache directory as cwd. ``-I`` keeps the script directory, the cwd and user
site-packages off ``sys.path``, so this file must stay self-contained: it may
import the standard library only, and never with a relative import. Tests and
the parent process still import it as ``miniclaw2.code_analysis`` to reach
the pure functions.

The analyser only ever reads files, tokenizes and ``ast.parse``s them. It
never imports, compiles or executes project code.

Two phases. Phase 1 reads every Python file and builds a compact summary —
enough to tell whether another file's results could change. Phase 2 resolves
names: scopes, bindings and a light type inference, re-parsing files on
demand through a small LRU rather than holding every tree in memory.

The governing rule is *do not guess*: a name or attribute is linked only when
its definition is certain. "Certainly outside the project" (an external base
class, a stdlib module) is a separate, hover-only verdict; anything that
cannot be inferred gets lexical colouring and nothing else.

All columns in the output are UTF-16 code units on newline-normalised text,
which is what a JavaScript string index counts.
"""

from __future__ import annotations

import ast
import builtins
import gzip
import hashlib
import inspect
import io
import json
import keyword
import os
import queue
import re
import sys
import threading
import tokenize
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

#: Bump whenever output for unchanged input could differ; it invalidates
#: every cache built by an older analyser.
ANALYZER_VERSION = 1
RESULT_FORMAT = 1
DOC_LIMIT = 4000
DEFAULT_LIMIT = 40
MAX_DEPTH = 64
LRU_SIZE = 64
ANALYSIS_CAP = 1024 * 1024

# Token types (shared with the frontend's `--tok-*` palette).
T_KEYWORD = 0
T_STRING = 1
T_NUMBER = 2
T_COMMENT = 3
T_OPERATOR = 4
T_DECORATOR = 5
T_MODULE = 6
T_CLASS = 7
T_FUNCTION = 8
T_METHOD = 9
T_PROPERTY = 10
T_PARAMETER = 11
T_VARIABLE = 12
T_CONSTANT = 13
T_TYPE_PARAMETER = 14
T_SELF_PARAMETER = 16

KIND_NAMES = {
    T_MODULE: "module",
    T_CLASS: "class",
    T_FUNCTION: "function",
    T_METHOD: "method",
    T_PROPERTY: "property",
    T_PARAMETER: "parameter",
    T_VARIABLE: "variable",
    T_CONSTANT: "constant",
    T_TYPE_PARAMETER: "typeParameter",
    T_SELF_PARAMETER: "parameter",
}

# Modifier bits.
M_DECLARATION = 1
M_EXTERNAL = 2
M_READONLY = 4
M_ASYNC = 8
M_DEPRECATED = 16
M_BUILTIN = 32

SOFT_KEYWORDS = frozenset({"match", "case", "type", "_"})
CONSTANT_NAMES = frozenset({"True", "False", "None"})
ALL_CAPS = re.compile(r"^_*[A-Z][A-Z0-9_]*$")
CAP_WORDS = re.compile(r"^_*[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*$")

FSTRING_START = getattr(tokenize, "FSTRING_START", None)
FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
FSTRING_END = getattr(tokenize, "FSTRING_END", None)
TSTRING_START = getattr(tokenize, "TSTRING_START", None)
TSTRING_MIDDLE = getattr(tokenize, "TSTRING_MIDDLE", None)
TSTRING_END = getattr(tokenize, "TSTRING_END", None)
TYPE_ALIAS = getattr(ast, "TypeAlias", None)


# ── source decoding and coordinates ──────────────────────────────────────


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def decode_source(raw: bytes, *, python: bool) -> str:
    """Decode bytes the way the reading page shows them.

    Python files honour PEP 263 (and a BOM); everything else is UTF-8.
    Undecodable bytes become U+FFFD rather than failing the read.
    """
    encoding = "utf-8"
    if python:
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        except (SyntaxError, LookupError):
            encoding = "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    return normalize_newlines(text)


class LineIndex:
    """Column conversions for one newline-normalised source.

    ``ast`` reports UTF-8 byte columns, ``tokenize`` code-point columns, and
    the browser wants UTF-16 code units. ASCII lines — nearly all of them —
    skip the tables entirely.
    """

    def __init__(self, text: str) -> None:
        self.lines = text.split("\n")
        self._byte_tables: dict[int, list[int]] = {}
        self._u16_tables: dict[int, list[int]] = {}

    def line(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1]
        return ""

    def byte_to_cp(self, lineno: int, col: int) -> int:
        text = self.line(lineno)
        if text.isascii():
            return col
        table = self._byte_tables.get(lineno)
        if table is None:
            # table[i] = byte offset of code point i
            table = [0]
            for char in text:
                table.append(table[-1] + len(char.encode("utf-8")))
            self._byte_tables[lineno] = table
        # Largest code point whose byte offset is <= col.
        low, high = 0, len(table) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if table[mid] <= col:
                low = mid
            else:
                high = mid - 1
        return low

    def cp_to_u16(self, lineno: int, col: int) -> int:
        text = self.line(lineno)
        if text.isascii():
            return col
        table = self._u16_tables.get(lineno)
        if table is None:
            table = [0]
            for char in text:
                table.append(table[-1] + (2 if ord(char) > 0xFFFF else 1))
            self._u16_tables[lineno] = table
        return table[min(col, len(table) - 1)]

    def u16_length(self, lineno: int) -> int:
        return self.cp_to_u16(lineno, len(self.line(lineno)))


# ── the inference lattice ────────────────────────────────────────────────


class Value:
    """A frozen description of what an expression evaluates to."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class UnknownV(Value):
    pass


UNKNOWN = UnknownV()


@dataclass(frozen=True, slots=True)
class ModuleV(Value):
    path: str
    name: str


@dataclass(frozen=True, slots=True)
class ClassV(Value):
    """A project class object (``instance=False``) or one of its instances."""

    path: str
    bid: int
    instance: bool = False


@dataclass(frozen=True, slots=True)
class FuncV(Value):
    path: str
    bid: int
    bound: bool = False


@dataclass(frozen=True, slots=True)
class BuiltinV(Value):
    """A ``builtins`` object, or an instance of a builtin type.

    ``args`` carries element types: one for list/set/frozenset, key and value
    for dict.
    """

    name: str
    instance: bool = False
    args: tuple[Value, ...] = ()


@dataclass(frozen=True, slots=True)
class ContainerV(Value):
    """An abstract iterable from an annotation (``Sequence[X]`` …)."""

    kind: str
    elem: Value


@dataclass(frozen=True, slots=True)
class MappingV(Value):
    kind: str
    key: Value
    value: Value


@dataclass(frozen=True, slots=True)
class TupleV(Value):
    items: tuple[Value, ...]


@dataclass(frozen=True, slots=True)
class AwaitableV(Value):
    inner: Value


@dataclass(frozen=True, slots=True)
class CallableV(Value):
    returns: Value


@dataclass(frozen=True, slots=True)
class SuperV(Value):
    path: str
    bid: int


@dataclass(frozen=True, slots=True)
class ExternalV(Value):
    qualname: str
    instance: bool = False
    #: How the name entered the file, for the naming-convention colour.
    via_import_module: bool = False


@dataclass(frozen=True, slots=True)
class UnionV(Value):
    items: tuple[Value, ...]


NONE_V = BuiltinV("None", instance=True)

BUILTIN_CONTAINERS = frozenset({"list", "set", "frozenset"})
ABSTRACT_CONTAINERS = frozenset({
    "List", "Sequence", "MutableSequence", "Iterable", "Iterator", "Generator",
    "AsyncIterator", "AsyncIterable", "AsyncGenerator", "Set", "AbstractSet",
    "MutableSet", "FrozenSet", "Deque", "deque", "Collection", "Reversible",
})
MAPPING_NAMES = frozenset({
    "dict", "Dict", "Mapping", "MutableMapping", "defaultdict", "DefaultDict",
    "OrderedDict", "Counter", "ChainMap",
})
WRAPPER_NAMES = frozenset({
    "Annotated", "ClassVar", "Final", "Required", "NotRequired", "ReadOnly",
    "InitVar",
})
OPAQUE_NAMES = frozenset({"Any", "object", "TypeVar"})
TRANSPARENT_DECORATORS = frozenset({
    "staticmethod", "classmethod", "functools.wraps", "functools.cache",
    "functools.lru_cache", "abc.abstractmethod", "typing.override",
    "typing.final", "typing_extensions.override", "typing_extensions.final",
    "wraps", "cache", "lru_cache", "abstractmethod", "override", "final",
})
PROPERTY_DECORATORS = frozenset({
    "property", "functools.cached_property", "cached_property",
})
ENUM_BASES = frozenset({
    "enum.Enum", "enum.StrEnum", "enum.IntEnum", "enum.Flag", "enum.IntFlag",
})
FIELD_CLASS_BASES = frozenset({
    "pydantic.BaseModel", "typing.NamedTuple", "typing.TypedDict",
    "typing_extensions.TypedDict", "typing_extensions.NamedTuple",
})
DATACLASS_DECORATORS = frozenset({
    "dataclasses.dataclass", "dataclass", "pydantic.dataclasses.dataclass",
})


def strip_none(value: Value) -> Value:
    """``X | None`` narrows to ``X`` (what Pylance does for member access)."""
    if isinstance(value, UnionV):
        rest = tuple(item for item in value.items if item != NONE_V)
        if not rest:
            return NONE_V
        if len(rest) == 1:
            return rest[0]
        return UnionV(rest)
    return value


def union(values: list[Value]) -> Value:
    flat: list[Value] = []
    for value in values:
        items = value.items if isinstance(value, UnionV) else (value,)
        for item in items:
            if item not in flat:
                flat.append(item)
    if not flat:
        return UNKNOWN
    if any(isinstance(item, UnknownV) for item in flat):
        return UNKNOWN
    if len(flat) == 1:
        return flat[0]
    return UnionV(tuple(flat))


# ── lexical layer ────────────────────────────────────────────────────────


@dataclass(slots=True)
class Lexed:
    #: (line, cp col) -> (string, cp end col) for every NAME token.
    names: dict[tuple[int, int], tuple[str, int]]
    #: line -> [(cp col, string)] in column order.
    name_lines: dict[int, list[tuple[int, str]]]
    #: Lexical spans that are not NAME tokens: (line, start cp, end cp, type).
    segments: list[tuple[int, int, int, int]]
    #: Where tokenizing stopped early, if it did: (line, cp col).
    stopped_at: tuple[int, int] | None


def _emit_span(
    segments: list[tuple[int, int, int, int]],
    lines: list[str],
    start: tuple[int, int],
    end: tuple[int, int],
    kind: int,
) -> None:
    """Add a possibly multi-line span, split so no segment crosses a line."""
    (sl, sc), (el, ec) = start, end
    if (sl, sc) >= (el, ec):
        return
    for line in range(sl, el + 1):
        text = lines[line - 1] if 1 <= line <= len(lines) else ""
        begin = sc if line == sl else 0
        finish = ec if line == el else len(text)
        if finish > begin:
            segments.append((line, begin, finish, kind))


def lex(text: str) -> Lexed:
    lines = text.split("\n")
    names: dict[tuple[int, int], tuple[str, int]] = {}
    name_lines: dict[int, list[tuple[int, str]]] = {}
    segments: list[tuple[int, int, int, int]] = []
    # f-string contexts: [start, holes, brace depth]. The literal parts are
    # coloured as "the whole f-string minus its replacement fields" rather
    # than from FSTRING_MIDDLE, whose end column is one short after `{{`
    # on 3.12+.
    fstack: list[list[Any]] = []
    starts = {t for t in (FSTRING_START, TSTRING_START) if t is not None}
    middles = {t for t in (FSTRING_MIDDLE, TSTRING_MIDDLE) if t is not None}
    ends = {t for t in (FSTRING_END, TSTRING_END) if t is not None}
    line_start = True
    stopped: tuple[int, int] | None = None
    last_end = (1, 0)
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    try:
        for tok in tokens:
            ttype, string = tok.type, tok.string
            last_end = tok.end
            if ttype in starts:
                fstack.append([tok.start, [], 0])
                line_start = False
                continue
            if fstack and ttype in middles:
                continue
            if fstack and ttype in ends:
                start, holes, _depth = fstack.pop()
                cursor = start
                for hole_start, hole_end in holes:
                    if hole_end is None:
                        continue
                    _emit_span(segments, lines, cursor, hole_start, T_STRING)
                    cursor = hole_end
                _emit_span(segments, lines, cursor, tok.end, T_STRING)
                continue
            if ttype == tokenize.OP:
                if fstack and string in "{}":
                    context = fstack[-1]
                    if string == "{":
                        context[2] += 1
                        if context[2] == 1:
                            context[1].append([tok.start, None])
                            _emit_span(segments, lines, tok.start, tok.end, T_OPERATOR)
                    else:
                        if context[2] == 1 and context[1]:
                            context[1][-1][1] = tok.end
                            _emit_span(segments, lines, tok.start, tok.end, T_OPERATOR)
                        context[2] = max(0, context[2] - 1)
                elif string == "@" and line_start:
                    _emit_span(segments, lines, tok.start, tok.end, T_DECORATOR)
                elif string == "->":
                    _emit_span(segments, lines, tok.start, tok.end, T_OPERATOR)
            elif ttype == tokenize.NAME:
                (line, col) = tok.start
                names[(line, col)] = (string, tok.end[1])
                name_lines.setdefault(line, []).append((col, string))
            elif ttype == tokenize.STRING:
                _emit_span(segments, lines, tok.start, tok.end, T_STRING)
            elif ttype == tokenize.NUMBER:
                _emit_span(segments, lines, tok.start, tok.end, T_NUMBER)
            elif ttype == tokenize.COMMENT:
                _emit_span(segments, lines, tok.start, tok.end, T_COMMENT)
            if ttype in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                line_start = True
            elif ttype not in (tokenize.NL, tokenize.COMMENT):
                line_start = False
    except (tokenize.TokenError, SyntaxError):
        # Keep what was coloured before the failure; the rest is plain text.
        stopped = last_end
    return Lexed(names, name_lines, segments, stopped)


# ── module naming and summaries ──────────────────────────────────────────


def module_name_for(path: str, python_files: set[str]) -> tuple[str, str]:
    """Return ``(dotted module name, package root directory)``.

    Walk up while the directory holds an ``__init__.py``; the first
    directory without one is the import root. A file outside any package is
    a top-level module rooted at its own directory.
    """
    parts_path = path.split("/")
    stem = parts_path[-1].rsplit(".", 1)[0]
    parts = [] if stem == "__init__" else [stem]
    directory = parts_path[:-1]
    while directory and (
        "/".join([*directory, "__init__.py"]) in python_files
        or "/".join([*directory, "__init__.pyi"]) in python_files
    ):
        parts.insert(0, directory[-1])
        directory = directory[:-1]
    return ".".join(parts), "/".join(directory)


def summary_digest(tree: ast.Module, module: str) -> str:
    """What other files could observe about this one.

    Positions are part of it, because definitions are jump targets, and
    function bodies are part of it, because unannotated returns and
    ``self.x = value`` are inferred from them. So only comment edits that
    keep every line in place leave it unchanged; any other change re-runs
    phase 2 for the whole project, which is cheap.
    """
    payload = module + "\n" + ast.dump(tree, include_attributes=True)
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


# ── per-file model: scopes and bindings ──────────────────────────────────


@dataclass(eq=False, slots=True)
class Scope:
    kind: str  # module | class | function | lambda | comprehension | typeparams
    node: ast.AST | None
    parent: "Scope | None"
    bindings: dict[str, list["Binding"]] = field(default_factory=dict)
    declared_global: set[str] = field(default_factory=set)
    declared_nonlocal: set[str] = field(default_factory=set)
    #: Names made local without a usable binding (``x += 1``, ``del x``).
    local_names: set[str] = field(default_factory=set)
    #: For a class scope, the class's own binding.
    class_binding: "Binding | None" = None


@dataclass(eq=False, slots=True)
class Binding:
    bid: int
    name: str
    #: def | class | param | assign | annassign | for | comp | with | except
    #: | walrus | import | importfrom | match | typeparam | selfattr
    kind: str
    scope: Scope
    node: ast.AST | None
    order: tuple[int, int]
    span: tuple[int, int, int] | None
    value: ast.expr | None = None
    value_scope: Scope | None = None
    annotation: ast.expr | None = None
    path: tuple[Any, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


class FileModel:
    def __init__(self, path: str, module: str, text: str, tree: ast.Module | None, lexed: Lexed) -> None:
        self.path = path
        self.module = module
        self.text = text
        self.tree = tree
        self.lexed = lexed
        self.index = LineIndex(text)
        self.bindings: list[Binding] = []
        self.module_scope = Scope("module", tree, None)
        self.node_scope: dict[int, Scope] = {}
        self.def_binding: dict[int, Binding] = {}
        self.class_scopes: dict[int, Scope] = {}
        #: Class bid -> attribute -> ``self.attr`` bindings, in source order.
        self.class_attrs: dict[int, dict[str, list[Binding]]] = {}
        #: id(FunctionDef) -> binding of the class it is a method of.
        self.func_class: dict[int, Binding] = {}
        self.parents: dict[int, ast.AST] = {}
        self.uses: list[tuple[ast.Name, Scope]] = []
        self.attrs: list[tuple[ast.Attribute, Scope]] = []
        self.calls: list[tuple[ast.Call, Scope]] = []
        self.declared: list[tuple[ast.AST, Scope, list[str]]] = []  # global/nonlocal
        self.import_modules: list[tuple[tuple[int, int, int], str, int, str]] = []
        self.import_names: list[tuple[tuple[int, int, int], Binding | None, str, int, str]] = []
        self.star_imports: list[tuple[str, int]] = []
        self.soft_keywords: list[tuple[int, int, int]] = []
        if tree is not None:
            ScopeBuilder(self).build(tree)

    # coordinates -------------------------------------------------------

    def pos(self, node: ast.AST) -> tuple[int, int]:
        line = node.lineno  # type: ignore[attr-defined]
        return line, self.index.byte_to_cp(line, node.col_offset)  # type: ignore[attr-defined]

    def end(self, node: ast.AST) -> tuple[int, int]:
        line = node.end_lineno  # type: ignore[attr-defined]
        return line, self.index.byte_to_cp(line, node.end_col_offset)  # type: ignore[attr-defined]

    def span_at(self, line: int, col: int, name: str | None = None) -> tuple[int, int, int] | None:
        hit = self.lexed.names.get((line, col))
        if hit is None or (name is not None and hit[0] != name):
            return None
        return line, col, hit[1]

    def find_name(
        self,
        name: str,
        start: tuple[int, int],
        end: tuple[int, int] | None = None,
        *,
        last: bool = False,
    ) -> tuple[int, int, int] | None:
        stop = end or (start[0] + 50, 1 << 30)
        found: tuple[int, int, int] | None = None
        for line in range(start[0], stop[0] + 1):
            for col, string in self.lexed.name_lines.get(line, ()):
                if (line, col) < start or (line, col) >= stop:
                    continue
                if string == name:
                    hit = (line, col, self.lexed.names[(line, col)][1])
                    if not last:
                        return hit
                    found = hit
        return found

    def names_between(self, start: tuple[int, int], stop: tuple[int, int]) -> list[tuple[int, int, int, str]]:
        out = []
        for line in range(start[0], stop[0] + 1):
            for col, string in self.lexed.name_lines.get(line, ()):
                if start <= (line, col) < stop:
                    out.append((line, col, self.lexed.names[(line, col)][1], string))
        return out

    def attr_span(self, node: ast.Attribute) -> tuple[int, int, int] | None:
        line, end = self.end(node)
        return self.span_at(line, end - len(node.attr), node.attr)

    def enclosing_class(self, scope: Scope | None) -> Binding | None:
        while scope is not None:
            if scope.kind == "class":
                return scope.class_binding
            if scope.kind == "function" and scope.node is not None:
                owner = self.func_class.get(id(scope.node))
                if owner is not None:
                    return owner
            scope = scope.parent
        return None


def _decorator_names(node: ast.AST) -> list[str]:
    names = []
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        try:
            names.append(ast.unparse(target))
        except Exception:  # noqa: BLE001
            names.append("")
    return names


def _comprehension_types() -> tuple[type, ...]:
    return (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


class ScopeBuilder:
    def __init__(self, model: FileModel) -> None:
        self.m = model

    def build(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            self.visit(stmt, self.m.module_scope, tree)

    # bindings ----------------------------------------------------------

    def bind(
        self,
        scope: Scope,
        name: str,
        kind: str,
        node: ast.AST | None,
        order: tuple[int, int],
        span: tuple[int, int, int] | None,
        **kwargs: Any,
    ) -> Binding:
        binding = Binding(len(self.m.bindings), name, kind, scope, node, order, span, **kwargs)
        self.m.bindings.append(binding)
        target = scope
        if name in scope.declared_global:
            target = self.m.module_scope
        target.bindings.setdefault(name, []).append(binding)
        if binding.scope is not target:
            binding.scope = target
        return binding

    def bind_target(
        self,
        target: ast.expr,
        scope: Scope,
        kind: str,
        value: ast.expr | None,
        order: tuple[int, int],
        path: tuple[Any, ...] = (),
        parent: ast.AST | None = None,
        **kwargs: Any,
    ) -> None:
        if parent is not None:
            self.m.parents[id(target)] = parent
        if isinstance(target, ast.Name):
            self.bind(
                scope, target.id, kind, target, order,
                self.m.span_at(*self.m.pos(target), target.id),
                value=value, value_scope=kwargs.pop("value_scope", scope), path=path, **kwargs,
            )
        elif isinstance(target, (ast.Tuple, ast.List)):
            star = next((i for i, elt in enumerate(target.elts) if isinstance(elt, ast.Starred)), None)
            for i, elt in enumerate(target.elts):
                if star is None or i < star:
                    step: Any = i
                elif i == star:
                    step = "star"
                else:
                    step = i - len(target.elts)
                self.bind_target(elt, scope, kind, value, order, (*path, step), target, **dict(kwargs))
        elif isinstance(target, ast.Starred):
            self.bind_target(target.value, scope, kind, value, order, path, target, **kwargs)
        else:
            # Attribute or subscript target: its own parts are loads.
            self.visit(target, scope, parent or target)
            if isinstance(target, ast.Attribute) and kind in ("assign", "annassign"):
                self.self_attribute(target, scope, value, kwargs.get("annotation"), order, path)

    def self_attribute(
        self,
        target: ast.Attribute,
        scope: Scope,
        value: ast.expr | None,
        annotation: ast.expr | None,
        order: tuple[int, int],
        path: tuple[Any, ...],
    ) -> None:
        if scope.kind != "function" or not isinstance(target.value, ast.Name):
            return
        func = scope.node
        owner = self.m.func_class.get(id(func)) if func is not None else None
        if owner is None or "staticmethod" in _decorator_names(func):
            return
        args = func.args  # type: ignore[union-attr]
        first = (args.posonlyargs + args.args)[:1]
        if not first or first[0].arg != target.value.id:
            return
        binding = Binding(
            len(self.m.bindings), target.attr, "selfattr", scope, target, order,
            self.m.attr_span(target), value=value, value_scope=scope,
            annotation=annotation, path=path,
            extra={"method": getattr(func, "name", ""), "owner": owner.bid},
        )
        self.m.bindings.append(binding)
        self.m.class_attrs.setdefault(owner.bid, {}).setdefault(target.attr, []).append(binding)

    # traversal ---------------------------------------------------------

    def visit_all(self, nodes: list[Any], scope: Scope, parent: ast.AST) -> None:
        for node in nodes:
            if node is not None:
                self.visit(node, scope, parent)

    def generic(self, node: ast.AST, scope: Scope) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child, scope, node)

    def visit(self, node: ast.AST, scope: Scope, parent: ast.AST) -> None:
        self.m.parents[id(node)] = parent
        handler = getattr(self, "visit_" + type(node).__name__, None)
        if handler is not None:
            handler(node, scope)
        else:
            self.generic(node, scope)

    def visit_Name(self, node: ast.Name, scope: Scope) -> None:
        if isinstance(node.ctx, ast.Load):
            self.m.uses.append((node, scope))
        elif isinstance(node.ctx, ast.Del):
            scope.local_names.add(node.id)
            self.m.uses.append((node, scope))
        else:
            self.bind(scope, node.id, "assign", node, self.m.pos(node), self.m.span_at(*self.m.pos(node), node.id))

    def visit_Attribute(self, node: ast.Attribute, scope: Scope) -> None:
        self.visit(node.value, scope, node)
        self.m.attrs.append((node, scope))

    def visit_Call(self, node: ast.Call, scope: Scope) -> None:
        self.generic(node, scope)
        if node.keywords:
            self.m.calls.append((node, scope))

    def _params(self, node: ast.AST, scope: Scope, owner: Binding | None) -> None:
        args = node.args  # type: ignore[attr-defined]
        order = self.m.pos(node)
        ordered = [
            *[(a, "posonly") for a in args.posonlyargs],
            *[(a, "positional") for a in args.args],
            *([(args.vararg, "vararg")] if args.vararg else []),
            *[(a, "kwonly") for a in args.kwonlyargs],
            *([(args.kwarg, "kwarg")] if args.kwarg else []),
        ]
        for index, (arg, role) in enumerate(ordered):
            self.m.parents[id(arg)] = node
            self.bind(
                scope, arg.arg, "param", arg, order, self.m.span_at(*self.m.pos(arg), arg.arg),
                annotation=arg.annotation,
                extra={"role": role, "index": index, "func": id(node), "method_of": owner.bid if owner else None},
            )

    def _type_params(self, node: ast.AST, scope: Scope) -> Scope:
        params = getattr(node, "type_params", None) or []
        if not params:
            return scope
        tscope = Scope("typeparams", node, scope)
        for param in params:
            self.m.parents[id(param)] = node
            self.bind(tscope, param.name, "typeparam", param, self.m.pos(param),
                      self.m.span_at(*self.m.pos(param), param.name))
            bound = getattr(param, "bound", None)
            if bound is not None:
                self.visit(bound, tscope, param)
        return tscope

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope: Scope) -> None:
        self.visit_all(node.decorator_list, scope, node)
        args = node.args
        self.visit_all([*args.defaults, *args.kw_defaults], scope, node)
        ann_scope = self._type_params(node, scope)
        for arg in [*args.posonlyargs, *args.args, args.vararg, *args.kwonlyargs, args.kwarg]:
            if arg is not None and arg.annotation is not None:
                self.visit(arg.annotation, ann_scope, arg)
        if node.returns is not None:
            self.visit(node.returns, ann_scope, node)
        owner = scope.class_binding if scope.kind == "class" else None
        binding = self.bind(
            scope, node.name, "def", node, self.m.pos(node),
            self.m.find_name(node.name, self.m.pos(node)),
            extra={"async": isinstance(node, ast.AsyncFunctionDef), "method_of": owner.bid if owner else None},
        )
        self.m.def_binding[id(node)] = binding
        fscope = Scope("function", node, ann_scope)
        self.m.node_scope[id(node)] = fscope
        if owner is not None:
            self.m.func_class[id(node)] = owner
        self._params(node, fscope, owner)
        self.visit_all(node.body, fscope, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda, scope: Scope) -> None:
        self.visit_all([*node.args.defaults, *node.args.kw_defaults], scope, node)
        lscope = Scope("lambda", node, scope)
        self.m.node_scope[id(node)] = lscope
        self._params(node, lscope, None)
        self.visit(node.body, lscope, node)

    def visit_ClassDef(self, node: ast.ClassDef, scope: Scope) -> None:
        self.visit_all(node.decorator_list, scope, node)
        tscope = self._type_params(node, scope)
        self.visit_all(node.bases, tscope, node)
        self.visit_all(node.keywords, tscope, node)
        binding = self.bind(scope, node.name, "class", node, self.m.pos(node),
                            self.m.find_name(node.name, self.m.pos(node)))
        self.m.def_binding[id(node)] = binding
        cscope = Scope("class", node, tscope, class_binding=binding)
        self.m.node_scope[id(node)] = cscope
        self.m.class_scopes[binding.bid] = cscope
        self.visit_all(node.body, cscope, node)

    def _comprehension(self, node: Any, scope: Scope) -> None:
        generators = node.generators
        self.visit(generators[0].iter, scope, node)
        cscope = Scope("comprehension", node, scope)
        self.m.node_scope[id(node)] = cscope
        for index, gen in enumerate(generators):
            self.m.parents[id(gen)] = node
            if index:
                self.visit(gen.iter, cscope, gen)
            self.bind_target(gen.target, cscope, "comp", gen.iter, self.m.pos(node), parent=gen,
                             value_scope=scope if index == 0 else cscope)
            self.visit_all(gen.ifs, cscope, gen)
        for part in ("elt", "key", "value"):
            child = getattr(node, part, None)
            if child is not None:
                self.visit(child, cscope, node)

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _comprehension

    def visit_Assign(self, node: ast.Assign, scope: Scope) -> None:
        self.visit(node.value, scope, node)
        for target in node.targets:
            self.bind_target(target, scope, "assign", node.value, self.m.end(node), parent=node)

    def visit_AnnAssign(self, node: ast.AnnAssign, scope: Scope) -> None:
        self.visit(node.annotation, scope, node)
        if node.value is not None:
            self.visit(node.value, scope, node)
        self.bind_target(node.target, scope, "annassign", node.value, self.m.end(node),
                         parent=node, annotation=node.annotation)

    def visit_AugAssign(self, node: ast.AugAssign, scope: Scope) -> None:
        self.visit(node.value, scope, node)
        if isinstance(node.target, ast.Name):
            self.m.parents[id(node.target)] = node
            scope.local_names.add(node.target.id)
            self.m.uses.append((node.target, scope))
        else:
            self.visit(node.target, scope, node)

    def visit_For(self, node: ast.For | ast.AsyncFor, scope: Scope) -> None:
        self.visit(node.iter, scope, node)
        self.bind_target(node.target, scope, "for", node.iter, self.m.end(node.iter), parent=node,
                         extra={"async": isinstance(node, ast.AsyncFor)})
        self.visit_all([*node.body, *node.orelse], scope, node)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With | ast.AsyncWith, scope: Scope) -> None:
        for item in node.items:
            self.m.parents[id(item)] = node
            self.visit(item.context_expr, scope, item)
            if item.optional_vars is not None:
                self.bind_target(item.optional_vars, scope, "with", item.context_expr,
                                 self.m.end(item.context_expr), parent=item,
                                 extra={"async": isinstance(node, ast.AsyncWith)})
        self.visit_all(node.body, scope, node)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler, scope: Scope) -> None:
        if node.type is not None:
            self.visit(node.type, scope, node)
        if node.name:
            start = self.m.end(node.type) if node.type is not None else self.m.pos(node)
            stop = self.m.pos(node.body[0]) if node.body else None
            self.bind(scope, node.name, "except", node, self.m.pos(node),
                      self.m.find_name(node.name, start, stop), value=node.type, value_scope=scope)
        self.visit_all(node.body, scope, node)

    def visit_NamedExpr(self, node: ast.NamedExpr, scope: Scope) -> None:
        self.visit(node.value, scope, node)
        target = scope
        while target.kind == "comprehension" and target.parent is not None:
            target = target.parent
        self.m.parents[id(node.target)] = node
        self.bind(target, node.target.id, "walrus", node.target, self.m.end(node),
                  self.m.span_at(*self.m.pos(node.target), node.target.id),
                  value=node.value, value_scope=scope)

    def visit_Import(self, node: ast.Import, scope: Scope) -> None:
        for alias in node.names:
            self.m.parents[id(alias)] = node
            start, stop = self.m.pos(alias), self.m.end(alias)
            names = self.m.names_between(start, stop)
            parts = alias.name.split(".")
            for index, part in enumerate(parts):
                if index < len(names) and names[index][3] == part:
                    span = names[index][:3]
                    self.m.import_modules.append((span, ".".join(parts[: index + 1]), 0, ""))
            if alias.asname:
                span = names[-1][:3] if names and names[-1][3] == alias.asname else None
                binding = self.bind(scope, alias.asname, "import", alias, start, span,
                                    extra={"module": alias.name})
                if span is not None:
                    self.m.import_names.append((span, binding, alias.name, -1, ""))
            else:
                span = names[0][:3] if names and names[0][3] == parts[0] else None
                self.bind(scope, parts[0], "import", alias, start, span, extra={"module": parts[0]})

    def visit_ImportFrom(self, node: ast.ImportFrom, scope: Scope) -> None:
        module = node.module or ""
        start = self.m.pos(node)
        first_alias = node.names[0]
        stop = self.m.pos(first_alias) if hasattr(first_alias, "lineno") else self.m.end(node)
        names = [n for n in self.m.names_between(start, stop) if n[3] not in ("from", "import")]
        parts = module.split(".") if module else []
        for index, part in enumerate(parts):
            if index < len(names) and names[index][3] == part:
                self.m.import_modules.append((names[index][:3], ".".join(parts[: index + 1]), node.level, ""))
        for alias in node.names:
            self.m.parents[id(alias)] = node
            if alias.name == "*":
                self.m.star_imports.append((module, node.level))
                continue
            alias_names = self.m.names_between(self.m.pos(alias), self.m.end(alias))
            name_span = alias_names[0][:3] if alias_names and alias_names[0][3] == alias.name else None
            bound = alias.asname or alias.name
            bound_span = name_span
            if alias.asname:
                bound_span = alias_names[-1][:3] if alias_names and alias_names[-1][3] == alias.asname else None
            binding = self.bind(scope, bound, "importfrom", alias, start, bound_span,
                                extra={"module": module, "level": node.level, "name": alias.name})
            if name_span is not None:
                self.m.import_names.append((name_span, binding, module, node.level, alias.name))
            if alias.asname and bound_span is not None:
                self.m.import_names.append((bound_span, binding, module, node.level, alias.name))

    def visit_Global(self, node: ast.Global | ast.Nonlocal, scope: Scope) -> None:
        target = scope.declared_global if isinstance(node, ast.Global) else scope.declared_nonlocal
        target.update(node.names)
        self.m.declared.append((node, scope, list(node.names)))

    visit_Nonlocal = visit_Global

    def visit_Match(self, node: Any, scope: Scope) -> None:
        span = self.m.span_at(*self.m.pos(node), "match")
        if span:
            self.m.soft_keywords.append(span)
        self.visit(node.subject, scope, node)
        for case in node.cases:
            self.m.parents[id(case)] = node
            pattern_pos = self.m.pos(case.pattern)
            hit = self.m.find_name("case", (max(1, pattern_pos[0] - 3), 0), pattern_pos, last=True)
            if hit:
                self.m.soft_keywords.append(hit)
            self.visit(case.pattern, scope, case)
            if case.guard is not None:
                self.visit(case.guard, scope, case)
            self.visit_all(case.body, scope, case)

    def visit_MatchAs(self, node: Any, scope: Scope) -> None:
        if node.pattern is not None:
            self.visit(node.pattern, scope, node)
        if node.name is None:
            if node.pattern is None:
                span = self.m.span_at(*self.m.pos(node), "_")
                if span:
                    self.m.soft_keywords.append(span)
            return
        span = self.m.find_name(node.name, self.m.pos(node), self.m.end(node), last=True)
        self.bind(scope, node.name, "match", node, self.m.pos(node), span)

    def visit_MatchStar(self, node: Any, scope: Scope) -> None:
        if node.name:
            span = self.m.find_name(node.name, self.m.pos(node), self.m.end(node), last=True)
            self.bind(scope, node.name, "match", node, self.m.pos(node), span)

    def visit_MatchMapping(self, node: Any, scope: Scope) -> None:
        self.visit_all([*node.keys, *node.patterns], scope, node)
        if node.rest:
            span = self.m.find_name(node.rest, self.m.pos(node), self.m.end(node), last=True)
            self.bind(scope, node.rest, "match", node, self.m.pos(node), span)

    def visit_TypeAlias(self, node: Any, scope: Scope) -> None:
        span = self.m.span_at(*self.m.pos(node), "type")
        if span:
            self.m.soft_keywords.append(span)
        tscope = self._type_params(node, scope)
        self.visit(node.value, tscope, node)
        name = node.name
        self.m.parents[id(name)] = node
        self.bind(scope, name.id, "assign", name, self.m.end(node),
                  self.m.span_at(*self.m.pos(name), name.id), value=node.value, value_scope=tscope,
                  extra={"type_alias": True})


# ── builtins introspection ───────────────────────────────────────────────


def clean_doc(doc: str | None) -> str | None:
    if not doc or not doc.strip():
        return None
    text = inspect.cleandoc(doc)
    if len(text) > DOC_LIMIT:
        text = text[:DOC_LIMIT].rstrip() + "…"
    return text


class BuiltinCatalog:
    """Signatures and docs of ``builtins``, computed once per run."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, str | None]] = {}

    @staticmethod
    def resolve(dotted: str) -> Any:
        obj: Any = builtins
        for part in dotted.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                return None
        return obj

    @staticmethod
    def is_class(name: str) -> bool:
        return isinstance(getattr(builtins, name, None), type)

    @staticmethod
    def has_member(type_name: str, attr: str) -> bool:
        owner = getattr(builtins, type_name, None)
        return isinstance(owner, type) and hasattr(owner, attr)

    def describe(self, dotted: str) -> tuple[str, str | None]:
        cached = self._cache.get(dotted)
        if cached is not None:
            return cached
        obj = self.resolve(dotted)
        method = "." in dotted
        is_class = isinstance(obj, type)
        params = None
        try:
            signature = inspect.signature(obj)
            parameters = list(signature.parameters.values())
            if method and parameters and parameters[0].name in ("self", "cls"):
                parameters = parameters[1:]
            params = str(signature.replace(parameters=parameters))
        except (TypeError, ValueError):
            text_sig = getattr(obj, "__text_signature__", None)
            if text_sig:
                params = text_sig.replace("$self, ", "").replace("($self)", "()").replace("$cls, ", "")
        doc = clean_doc(getattr(obj, "__doc__", None))
        kind = "class" if is_class else ("method" if method else "function")
        if params is None and doc:
            first = doc.splitlines()[0]
            if "(" in first:
                signature_text = f"({kind}) {first.split(' -> ')[0] if is_class else first}"
                self._cache[dotted] = (signature_text, doc)
                return self._cache[dotted]
        signature_text = f"({kind}) {dotted}{params or ('()' if not isinstance(obj, (int, str)) else '')}"
        if not callable(obj):
            signature_text = f"(variable) {dotted}"
        self._cache[dotted] = (signature_text, doc)
        return self._cache[dotted]


BUILTIN_FUNC_RETURNS = {
    "len": "int", "repr": "str", "ascii": "str", "format": "str", "chr": "str",
    "ord": "int", "hash": "int", "id": "int", "isinstance": "bool",
    "issubclass": "bool", "hasattr": "bool", "callable": "bool", "hex": "str",
    "oct": "str", "bin": "str", "input": "str",
}
STR_TO_STR = frozenset({
    "strip", "lstrip", "rstrip", "lower", "upper", "replace", "format", "join",
    "removeprefix", "removesuffix", "title", "capitalize", "casefold", "center",
    "ljust", "rjust", "zfill", "expandtabs", "swapcase", "translate", "format_map",
})
STR_TO_LIST = frozenset({"split", "rsplit", "splitlines"})
TO_BOOL = frozenset({
    "startswith", "endswith", "isdigit", "isalpha", "isalnum", "isspace",
    "islower", "isupper", "isidentifier", "isnumeric", "isdecimal", "isascii",
    "istitle", "isprintable", "isdisjoint", "issubset", "issuperset",
})
TO_INT = frozenset({"find", "rfind", "index", "rindex", "count"})


def builtin_method_returns(type_name: str, method: str, receiver: BuiltinV) -> Value:
    args = receiver.args
    if method in TO_BOOL:
        return BuiltinV("bool", True)
    if method in TO_INT and type_name in ("str", "bytes", "list", "tuple"):
        return BuiltinV("int", True)
    if type_name == "str":
        if method in STR_TO_STR:
            return BuiltinV("str", True)
        if method in STR_TO_LIST:
            return BuiltinV("list", True, (BuiltinV("str", True),))
        if method == "encode":
            return BuiltinV("bytes", True)
        if method == "partition" or method == "rpartition":
            s = BuiltinV("str", True)
            return TupleV((s, s, s))
    if type_name == "bytes" and method == "decode":
        return BuiltinV("str", True)
    if type_name == "dict" and len(args) == 2:
        key, value = args
        if method == "get":
            return union([value, NONE_V])
        if method in ("pop", "setdefault"):
            return value
        if method == "items":
            return BuiltinV("dict_items", True, (key, value))
        if method == "keys":
            return BuiltinV("dict_keys", True, (key,))
        if method == "values":
            return BuiltinV("dict_values", True, (value,))
        if method == "copy":
            return receiver
    if type_name in ("list", "set") and args:
        if method == "pop":
            return args[0]
        if method == "copy":
            return receiver
    return UNKNOWN


# ── references and the project resolver ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class Ref:
    origin: str  # project | module | builtin | external
    path: str = ""
    bid: int = -1
    name: str = ""
    #: External names: the convention-derived kind ("class", "function" …).
    hint: str = ""


TYPING_MODULES = frozenset({"typing", "typing_extensions", "collections.abc", "collections", "builtins", "types"})
IGNORED_EXTERNAL_BASES = frozenset({
    "typing.Generic", "typing.Protocol", "typing_extensions.Protocol", "abc.ABC",
    "builtins.object",
})


def typing_short(value: Value) -> str | None:
    """``ExternalV('typing.Optional')`` → ``'Optional'``; builtins by name."""
    if isinstance(value, ExternalV) and not value.instance:
        module, _, short = value.qualname.rpartition(".")
        if module in TYPING_MODULES:
            return short
    if isinstance(value, BuiltinV) and not value.instance:
        return value.name
    return None


class ProjectIndex:
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts
        files = set(texts)
        self.module_of: dict[str, str] = {}
        self.pkg_root: dict[str, str] = {}
        self.modules: dict[str, list[str]] = {}
        for path in sorted(files):
            module, root = module_name_for(path, files)
            if not module:
                continue
            self.module_of[path] = module
            self.pkg_root[path] = root
            self.modules.setdefault(module, []).append(path)
        self.top_level = {name.split(".")[0] for name in self.modules}
        self.stdlib = set(getattr(sys, "stdlib_module_names", ()))
        self._models: OrderedDict[str, FileModel] = OrderedDict()
        self._values: dict[tuple[str, int], Value] = {}
        self._refs: dict[tuple[str, int], Ref | None] = {}
        self._busy: set[tuple[str, Any]] = set()
        self._mro: dict[tuple[str, int], tuple[list[tuple[str, int]], list[str], list[str], bool]] = {}
        self._returns: dict[tuple[str, int], Value] = {}
        self.depth = 0
        self.builtins = BuiltinCatalog()

    # models ------------------------------------------------------------

    def model(self, path: str) -> FileModel | None:
        cached = self._models.get(path)
        if cached is not None:
            self._models.move_to_end(path)
            return cached
        text = self.texts.get(path)
        if text is None:
            return None
        try:
            tree: ast.Module | None = ast.parse(text, filename=path)
        except (SyntaxError, ValueError, RecursionError):
            tree = None
        model = FileModel(path, self.module_of.get(path, ""), text, tree, lex(text))
        self._models[path] = model
        if len(self._models) > LRU_SIZE:
            self._models.popitem(last=False)
        return model

    def binding(self, path: str, bid: int) -> tuple[FileModel, Binding] | None:
        model = self.model(path)
        if model is None or not 0 <= bid < len(model.bindings):
            return None
        return model, model.bindings[bid]

    # modules -----------------------------------------------------------

    def resolve_module(self, dotted: str, from_path: str | None) -> str | None:
        candidates = self.modules.get(dotted)
        if not candidates:
            return None
        if from_path is not None:
            same = [c for c in candidates if self.pkg_root.get(c) == self.pkg_root.get(from_path)]
            if dotted.split(".")[0] in self.stdlib and not same:
                # `import ast` from elsewhere is the stdlib, not tools/ast.py.
                return None
            if same:
                candidates = same

        def key(candidate: str) -> tuple[int, int, int, str]:
            common = 0
            if from_path is not None:
                for a, b in zip(candidate.split("/"), from_path.split("/")):
                    if a != b:
                        break
                    common += 1
            return (candidate.endswith(".pyi"), -common, len(candidate), candidate)

        return sorted(candidates, key=key)[0]

    def module_value(self, dotted: str, from_path: str | None) -> Value:
        if not dotted:
            return UNKNOWN
        path = self.resolve_module(dotted, from_path)
        if path is not None:
            return ModuleV(path, dotted)
        top = dotted.split(".")[0]
        if top in self.top_level and not (top in self.stdlib and self.resolve_module(top, from_path) is None):
            return UNKNOWN
        return ExternalV(dotted, via_import_module=True)

    def relative_base(self, path: str, level: int, module: str) -> str | None:
        if level == 0:
            return module
        own = self.module_of.get(path, "")
        package = own if path.rsplit("/", 1)[-1].startswith("__init__.") else own.rpartition(".")[0]
        parts = package.split(".") if package else []
        if level - 1 > len(parts):
            return None
        parts = parts[: len(parts) - (level - 1)]
        if module:
            parts.append(module)
        return ".".join(parts)

    def module_scope_binding(self, path: str, name: str) -> tuple[FileModel, list[Binding]] | None:
        model = self.model(path)
        if model is None:
            return None
        found = model.module_scope.bindings.get(name)
        return (model, found) if found else None

    def module_member(self, path: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()) -> tuple[Value, Ref | None]:
        key = (path, name)
        if key in seen or len(seen) > MAX_DEPTH:
            return UNKNOWN, None
        seen = seen | {key}
        hit = self.module_scope_binding(path, name)
        if hit is not None:
            model, found = hit
            first = found[0]
            values = [self.binding_value(path, b.bid, seen) for b in found]
            return union(values) if len(values) > 1 else values[0], self.binding_ref(path, first, seen)
        model = self.model(path)
        if model is not None:
            for module, level in model.star_imports:
                base = self.relative_base(path, level, module)
                target = self.module_value(base, path) if base else UNKNOWN
                if isinstance(target, ModuleV) and self.exports(target.path, name):
                    return self.module_member(target.path, name, seen)
        own = self.module_of.get(path)
        if own and path.rsplit("/", 1)[-1].startswith("__init__."):
            sub = self.resolve_module(f"{own}.{name}", path)
            if sub is not None:
                return ModuleV(sub, f"{own}.{name}"), Ref("module", sub, name=f"{own}.{name}")
        return UNKNOWN, None

    def exports(self, path: str, name: str) -> bool:
        model = self.model(path)
        if model is None or model.tree is None:
            return False
        for stmt in model.tree.body:
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets):
                if isinstance(stmt.value, (ast.List, ast.Tuple)):
                    return any(isinstance(e, ast.Constant) and e.value == name for e in stmt.value.elts)
        return not name.startswith("_") and name in model.module_scope.bindings

    # bindings ----------------------------------------------------------

    def binding_ref(self, path: str, binding: Binding, seen: frozenset[tuple[str, str]] = frozenset()) -> Ref | None:
        """The definition a binding finally names, following re-exports."""
        if binding.kind == "import":
            module = binding.extra["module"]
            value = self.module_value(module, path)
            if isinstance(value, ModuleV):
                return Ref("module", value.path, name=module)
            if isinstance(value, ExternalV):
                return Ref("external", name=module, hint="module")
            return None
        if binding.kind == "importfrom":
            base = self.relative_base(path, binding.extra["level"], binding.extra["module"])
            if base is None:
                return None
            value = self.module_value(base, path)
            name = binding.extra["name"]
            if isinstance(value, ModuleV):
                return self.module_member(value.path, name, seen)[1]
            if isinstance(value, ExternalV):
                return Ref("external", name=f"{base}.{name}")
            return None
        return Ref("project", path, binding.bid)

    def binding_value(self, path: str, bid: int, seen: frozenset[tuple[str, str]] = frozenset()) -> Value:
        key = (path, bid)
        cached = self._values.get(key)
        if cached is not None:
            return cached
        marker = ("binding", key)
        if marker in self._busy or self.depth > MAX_DEPTH:
            return UNKNOWN
        hit = self.binding(path, bid)
        if hit is None:
            return UNKNOWN
        model, binding = hit
        self._busy.add(marker)
        self.depth += 1
        try:
            value = self._binding_value(model, binding, seen)
        except RecursionError:
            value = UNKNOWN
        finally:
            self.depth -= 1
            self._busy.discard(marker)
        self._values[key] = value
        return value

    def _binding_value(self, m: FileModel, b: Binding, seen: frozenset[tuple[str, str]]) -> Value:
        kind = b.kind
        if kind == "def":
            return FuncV(m.path, b.bid)
        if kind == "class":
            return ClassV(m.path, b.bid)
        if kind == "import":
            return self.module_value(b.extra["module"], m.path)
        if kind == "importfrom":
            base = self.relative_base(m.path, b.extra["level"], b.extra["module"])
            if base is None:
                return UNKNOWN
            target = self.module_value(base, m.path)
            name = b.extra["name"]
            if isinstance(target, ModuleV):
                return self.module_member(target.path, name, seen)[0]
            if isinstance(target, ExternalV):
                return ExternalV(f"{base}.{name}")
            return UNKNOWN
        if kind == "param":
            return self.param_value(m, b)
        if kind in ("assign", "annassign", "walrus", "selfattr"):
            if b.annotation is not None:
                if b.extra.get("type_alias"):
                    return UNKNOWN
                return self.annotation(m, b.annotation, b.value_scope or b.scope)
            if b.value is None:
                return UNKNOWN
            return self.apply_path(self.infer(m, b.value, b.value_scope or b.scope), b.path)
        if kind in ("for", "comp"):
            assert b.value is not None
            return self.apply_path(self.iter_elem(self.infer(m, b.value, b.value_scope or b.scope)), b.path)
        if kind == "with":
            assert b.value is not None
            entered = self.enter(self.infer(m, b.value, b.value_scope or b.scope), b.extra.get("async", False))
            return self.apply_path(entered, b.path)
        if kind == "except":
            if b.value is None:
                return UNKNOWN
            return self.instance_of(self.infer(m, b.value, b.value_scope or b.scope))
        return UNKNOWN

    def param_value(self, m: FileModel, b: Binding) -> Value:
        func = m.parents.get(id(b.node))
        role = b.extra.get("role")
        if b.annotation is not None:
            scope = b.scope.parent or b.scope
            value = self.annotation(m, b.annotation, scope)
            if role == "vararg":
                return ContainerV("tuple", value)
            if role == "kwarg":
                return BuiltinV("dict", True, (BuiltinV("str", True), value))
            return value
        owner_bid = b.extra.get("method_of")
        if owner_bid is not None and b.extra.get("index") == 0 and role in ("posonly", "positional"):
            decorators = _decorator_names(func)
            if "staticmethod" in decorators:
                return UNKNOWN
            name = getattr(func, "name", "")
            if "classmethod" in decorators or name in ("__new__", "__init_subclass__", "__class_getitem__"):
                return ClassV(m.path, owner_bid)
            return ClassV(m.path, owner_bid, instance=True)
        return UNKNOWN

    # name lookup -------------------------------------------------------

    def lookup(self, m: FileModel, name: str, scope: Scope, pos: tuple[int, int]) -> Binding | str | None:
        """The binding a name refers to, ``"builtin"``, or ``None``."""
        current: Scope | None = scope
        if name in scope.declared_global:
            current = m.module_scope
        elif name in scope.declared_nonlocal:
            current = scope.parent
            while current is not None and (current.kind == "class" or name not in current.bindings):
                current = current.parent
        first = True
        while current is not None:
            if current.kind == "class" and not first:
                current = current.parent
                continue
            found = current.bindings.get(name)
            if found:
                before = [b for b in found if b.order <= pos]
                return before[-1] if before else found[0]
            if name in current.local_names:
                return None
            first = False
            current = current.parent
        for module, level in m.star_imports:
            base = self.relative_base(m.path, level, module)
            target = self.module_value(base, m.path) if base else UNKNOWN
            if isinstance(target, ModuleV) and self.exports(target.path, name):
                star = Binding(-1, name, "star", m.module_scope, None, (0, 0), None,
                               extra={"module": target.path})
                return star
        if hasattr(builtins, name):
            return "builtin"
        return None

    def name_value(self, m: FileModel, node: ast.Name, scope: Scope) -> tuple[Value, Ref | None]:
        narrowed = self.narrowed(m, node, scope)
        target = self.lookup(m, node.id, scope, m.pos(node))
        if target == "builtin":
            return BuiltinV(node.id), Ref("builtin", name=node.id)
        if target is None or isinstance(target, str):
            return UNKNOWN, None
        if target.kind == "star":
            value, ref = self.module_member(target.extra["module"], node.id)
            return (narrowed or value), ref
        value = narrowed or self.binding_value(m.path, target.bid)
        return value, self.binding_ref(m.path, target)

    def narrowed(self, m: FileModel, node: ast.Name, scope: Scope) -> Value | None:
        """``isinstance`` narrowing at a use site; ``None`` when none applies."""
        child: ast.AST = node
        parent = m.parents.get(id(child))
        while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.Module)):
            if isinstance(parent, ast.If) and child in parent.body:
                hit = self._isinstance_test(m, parent.test, node.id, scope)
                if hit is not None:
                    return hit
            if isinstance(parent, ast.IfExp) and child is parent.body:
                hit = self._isinstance_test(m, parent.test, node.id, scope)
                if hit is not None:
                    return hit
            if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.And):
                for value in parent.values:
                    if value is child:
                        break
                    hit = self._isinstance_test(m, value, node.id, scope)
                    if hit is not None:
                        return hit
            for block in ("body", "orelse", "finalbody"):
                statements = getattr(parent, block, None)
                if isinstance(statements, list) and child in statements:
                    for stmt in reversed(statements[: statements.index(child)]):
                        hit = self._block_narrowing(m, stmt, node.id, scope)
                        if hit is not None:
                            return hit
            child, parent = parent, m.parents.get(id(parent))
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            statements = parent.body
            if child in statements:
                for stmt in reversed(statements[: statements.index(child)]):
                    hit = self._block_narrowing(m, stmt, node.id, scope)
                    if hit is not None:
                        return hit
        return None

    def _block_narrowing(self, m: FileModel, stmt: ast.stmt, name: str, scope: Scope) -> Value | None:
        if isinstance(stmt, ast.Assert):
            return self._isinstance_test(m, stmt.test, name, scope)
        if (
            isinstance(stmt, ast.If)
            and isinstance(stmt.test, ast.UnaryOp)
            and isinstance(stmt.test.op, ast.Not)
            and stmt.body
            and isinstance(stmt.body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))
        ):
            return self._isinstance_test(m, stmt.test.operand, name, scope)
        return None

    def _isinstance_test(self, m: FileModel, test: ast.expr, name: str, scope: Scope) -> Value | None:
        tests = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
        for item in tests:
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id == "isinstance"
                and len(item.args) == 2
                and isinstance(item.args[0], ast.Name)
                and item.args[0].id == name
            ):
                value = self.instance_of(self.infer(m, item.args[1], scope))
                return None if isinstance(value, UnknownV) else value
        return None

    # expressions -------------------------------------------------------

    def infer(self, m: FileModel, node: ast.expr | None, scope: Scope) -> Value:
        if node is None or self.depth > MAX_DEPTH:
            return UNKNOWN
        self.depth += 1
        try:
            return self._infer(m, node, scope)
        except RecursionError:
            return UNKNOWN
        finally:
            self.depth -= 1

    def _infer(self, m: FileModel, node: ast.expr, scope: Scope) -> Value:
        if isinstance(node, ast.Name):
            return self.name_value(m, node, scope)[0]
        if isinstance(node, ast.Constant):
            value = node.value
            if value is None:
                return NONE_V
            if value is Ellipsis:
                return UNKNOWN
            return BuiltinV(type(value).__name__, True)
        if isinstance(node, ast.JoinedStr):
            return BuiltinV("str", True)
        if isinstance(node, (ast.List, ast.Set)):
            kind = "list" if isinstance(node, ast.List) else "set"
            elems = [self.infer(m, e, scope) for e in node.elts if not isinstance(e, ast.Starred)]
            elem = union(elems) if elems and len(set(elems)) == 1 else UNKNOWN
            return BuiltinV(kind, True, (elem,))
        if isinstance(node, ast.Dict):
            keys = [self.infer(m, k, scope) for k in node.keys if k is not None]
            values = [self.infer(m, v, scope) for k, v in zip(node.keys, node.values) if k is not None]
            key = keys[0] if keys and len(set(keys)) == 1 else UNKNOWN
            value = values[0] if values and len(set(values)) == 1 else UNKNOWN
            return BuiltinV("dict", True, (key, value))
        if isinstance(node, ast.Tuple):
            return TupleV(tuple(self.infer(m, e, scope) for e in node.elts))
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            inner = m.node_scope.get(id(node), scope)
            elem = self.infer(m, node.elt, inner)
            if isinstance(node, ast.GeneratorExp):
                return ContainerV("Generator", elem)
            return BuiltinV("list" if isinstance(node, ast.ListComp) else "set", True, (elem,))
        if isinstance(node, ast.DictComp):
            inner = m.node_scope.get(id(node), scope)
            return BuiltinV("dict", True, (self.infer(m, node.key, inner), self.infer(m, node.value, inner)))
        if isinstance(node, ast.Attribute):
            return self.member(self.infer(m, node.value, scope), node.attr)[0]
        if isinstance(node, ast.Call):
            return self.call(m, node, scope)
        if isinstance(node, ast.Await):
            value = self.infer(m, node.value, scope)
            return value.inner if isinstance(value, AwaitableV) else UNKNOWN
        if isinstance(node, ast.Subscript):
            return self.subscript(self.infer(m, node.value, scope), node.slice)
        if isinstance(node, ast.IfExp):
            return strip_none(union([self.infer(m, node.body, scope), self.infer(m, node.orelse, scope)]))
        if isinstance(node, ast.BoolOp):
            return strip_none(union([self.infer(m, v, scope) for v in node.values]))
        if isinstance(node, ast.NamedExpr):
            return self.infer(m, node.value, scope)
        if isinstance(node, ast.Compare):
            return BuiltinV("bool", True)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return BuiltinV("bool", True)
        return UNKNOWN

    def subscript(self, value: Value, index: ast.expr) -> Value:
        value = strip_none(value)
        if isinstance(value, BuiltinV) and value.instance:
            if value.name in ("list",) and value.args:
                return value.args[0] if not isinstance(index, ast.Slice) else value
            if value.name == "dict" and len(value.args) == 2:
                return value.args[1]
            if value.name == "str":
                return value
        if isinstance(value, ContainerV):
            return value.elem if not isinstance(index, ast.Slice) else UNKNOWN
        if isinstance(value, TupleV) and isinstance(index, ast.Constant) and isinstance(index.value, int):
            try:
                return value.items[index.value]
            except IndexError:
                return UNKNOWN
        if isinstance(value, ClassV) and value.instance:
            method = self.find_member(value, "__getitem__")
            if method is not None and method[0].kind == "def":
                return self.function_returns(method[1], method[0].bid, value)
        return UNKNOWN

    def call(self, m: FileModel, node: ast.Call, scope: Scope) -> Value:
        func = strip_none(self.infer(m, node.func, scope))
        if isinstance(func, ClassV) and not func.instance:
            return ClassV(func.path, func.bid, instance=True)
        if isinstance(func, FuncV):
            return self.function_returns(func.path, func.bid, None)
        if isinstance(func, CallableV):
            return func.returns
        if isinstance(func, BuiltinMethodV):
            return builtin_method_returns(func.type_name, func.method, func.receiver)
        if isinstance(func, BuiltinV) and not func.instance:
            return self.builtin_call(m, func.name, node, scope)
        return UNKNOWN

    def builtin_call(self, m: FileModel, name: str, node: ast.Call, scope: Scope) -> Value:
        args = node.args
        first = self.infer(m, args[0], scope) if args else UNKNOWN
        if name == "super":
            owner = m.enclosing_class(scope)
            return SuperV(m.path, owner.bid) if owner is not None else UNKNOWN
        if name in BUILTIN_FUNC_RETURNS:
            return BuiltinV(BUILTIN_FUNC_RETURNS[name], True)
        if name == "type" and len(args) == 1 and isinstance(first, ClassV) and first.instance:
            return ClassV(first.path, first.bid)
        if name in ("sorted", "list") and args:
            return BuiltinV("list", True, (self.iter_elem(first),))
        if name in ("set", "frozenset") and args:
            return BuiltinV(name, True, (self.iter_elem(first),))
        if name == "enumerate" and args:
            return BuiltinV("enumerate", True, (self.iter_elem(first),))
        if name == "zip":
            return BuiltinV("zip", True, tuple(self.iter_elem(self.infer(m, a, scope)) for a in args))
        if name == "reversed" and args:
            return BuiltinV("reversed", True, (self.iter_elem(first),))
        if name == "range":
            return BuiltinV("range", True)
        if name == "dict" and not args and not node.keywords:
            return BuiltinV("dict", True, (UNKNOWN, UNKNOWN))
        if BuiltinCatalog.is_class(name):
            return BuiltinV(name, True)
        return UNKNOWN

    def function_returns(self, path: str, bid: int, receiver: Value | None) -> Value:
        key = (path, bid)
        if key in self._returns:
            value = self._returns[key]
        else:
            marker = ("returns", key)
            if marker in self._busy:
                return UNKNOWN
            self._busy.add(marker)
            try:
                value = self._function_returns(path, bid)
            finally:
                self._busy.discard(marker)
            self._returns[key] = value
        if isinstance(value, SelfV):
            if isinstance(receiver, ClassV):
                return ClassV(receiver.path, receiver.bid, instance=True)
            return ClassV(value.path, value.bid, instance=True)
        if isinstance(value, AwaitableV) and isinstance(value.inner, SelfV):
            inner = value.inner
            if isinstance(receiver, ClassV):
                return AwaitableV(ClassV(receiver.path, receiver.bid, instance=True))
            return AwaitableV(ClassV(inner.path, inner.bid, instance=True))
        return value

    def _function_returns(self, path: str, bid: int) -> Value:
        hit = self.binding(path, bid)
        if hit is None:
            return UNKNOWN
        m, b = hit
        node = b.node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return UNKNOWN
        decorators = _decorator_names(node)
        if any(d not in TRANSPARENT_DECORATORS and d not in PROPERTY_DECORATORS and not d.endswith((".setter", ".deleter")) for d in decorators):
            return UNKNOWN
        fscope = m.node_scope.get(id(node))
        is_async = isinstance(node, ast.AsyncFunctionDef)
        if node.returns is not None:
            value = self.annotation(m, node.returns, (fscope.parent if fscope else b.scope) or b.scope, self_marker=True)
        else:
            if fscope is None or _is_generator(node):
                return UNKNOWN
            values = [
                self.infer(m, ret.value, fscope) if ret.value is not None else NONE_V
                for ret in _returns(node)
            ]
            if not values:
                value = NONE_V
            else:
                non_none = [v for v in values if v != NONE_V]
                if not non_none:
                    value = NONE_V
                elif any(isinstance(v, UnknownV) for v in non_none) or len(set(non_none)) != 1:
                    return UNKNOWN
                else:
                    value = non_none[0] if len(non_none) == len(values) else union([non_none[0], NONE_V])
        return AwaitableV(value) if is_async else value

    # annotations -------------------------------------------------------

    def annotation(self, m: FileModel, node: ast.expr | None, scope: Scope, *, self_marker: bool = False) -> Value:
        if node is None or self.depth > MAX_DEPTH:
            return UNKNOWN
        self.depth += 1
        try:
            return self._annotation(m, node, scope, self_marker)
        except RecursionError:
            return UNKNOWN
        finally:
            self.depth -= 1

    def _annotation(self, m: FileModel, node: ast.expr, scope: Scope, self_marker: bool) -> Value:
        if isinstance(node, ast.Constant):
            if node.value is None:
                return NONE_V
            if isinstance(node.value, str):
                try:
                    parsed = ast.parse(node.value.strip(), mode="eval").body
                except SyntaxError:
                    return UNKNOWN
                return self._annotation(m, _relocate(parsed, node), scope, self_marker)
            return UNKNOWN
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self.annotation(m, node.left, scope, self_marker=self_marker)
            right = self.annotation(m, node.right, scope, self_marker=self_marker)
            return union([left, right])
        if isinstance(node, ast.Subscript):
            head = self.infer(m, node.value, scope)
            short = typing_short(head)
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            ann = lambda n: self.annotation(m, n, scope, self_marker=self_marker)  # noqa: E731
            if short == "Optional":
                return union([ann(args[0]), NONE_V])
            if short == "Union":
                return union([ann(a) for a in args])
            if short in WRAPPER_NAMES:
                return ann(args[0])
            if short in ("type", "Type"):
                inner = ann(args[0])
                if isinstance(inner, ClassV):
                    return ClassV(inner.path, inner.bid)
                return UNKNOWN
            if short in BUILTIN_CONTAINERS:
                return BuiltinV(short, True, (ann(args[0]),))
            if short in ("list", "List"):
                return BuiltinV("list", True, (ann(args[0]),))
            if short in ("set", "Set", "frozenset", "FrozenSet") and isinstance(head, BuiltinV):
                return BuiltinV(short.lower(), True, (ann(args[0]),))
            if short in ABSTRACT_CONTAINERS:
                return ContainerV(short, ann(args[0]))
            if short in ("tuple", "Tuple"):
                if len(args) == 2 and isinstance(args[1], ast.Constant) and args[1].value is Ellipsis:
                    return ContainerV("tuple", ann(args[0]))
                return TupleV(tuple(ann(a) for a in args))
            if short in ("dict", "Dict") and len(args) == 2:
                return BuiltinV("dict", True, (ann(args[0]), ann(args[1])))
            if short in MAPPING_NAMES and len(args) == 2:
                return MappingV(short, ann(args[0]), ann(args[1]))
            if short == "Callable" and len(args) == 2:
                return CallableV(ann(args[1]))
            if short == "Awaitable":
                return AwaitableV(ann(args[0]))
            if short == "Coroutine" and len(args) == 3:
                return AwaitableV(ann(args[2]))
            # A generic project class, e.g. `Box[int]`.
            if isinstance(head, ClassV) and not head.instance:
                return ClassV(head.path, head.bid, instance=True)
            if isinstance(head, ExternalV) and not head.instance:
                return ExternalV(head.qualname, instance=True)
            return UNKNOWN
        value = self.infer(m, node, scope)
        short = typing_short(value)
        if short == "Self":
            owner = m.enclosing_class(scope)
            if owner is None:
                return UNKNOWN
            return SelfV(m.path, owner.bid) if self_marker else ClassV(m.path, owner.bid, instance=True)
        if short in OPAQUE_NAMES or short in ("None", "NoReturn", "Never"):
            return NONE_V if short == "None" else UNKNOWN
        if short in ("dict", "Dict"):
            return BuiltinV("dict", True, (UNKNOWN, UNKNOWN))
        return self.instance_of(value)

    def instance_of(self, value: Value) -> Value:
        if isinstance(value, ClassV) and not value.instance:
            hit = self.binding(value.path, value.bid)
            if hit is not None and _is_typevar(hit[1]):
                return UNKNOWN
            return ClassV(value.path, value.bid, instance=True)
        if isinstance(value, BuiltinV) and not value.instance and BuiltinCatalog.is_class(value.name):
            return BuiltinV(value.name, True)
        if isinstance(value, ExternalV) and not value.instance:
            return ExternalV(value.qualname, instance=True)
        if isinstance(value, TupleV):
            return union([self.instance_of(v) for v in value.items])
        if isinstance(value, UnionV):
            return union([self.instance_of(v) for v in value.items])
        return UNKNOWN

    # iteration and unpacking ------------------------------------------

    def iter_elem(self, value: Value) -> Value:
        value = strip_none(value)
        if isinstance(value, BuiltinV) and value.instance:
            name, args = value.name, value.args
            if name in ("list", "set", "frozenset", "reversed", "dict_keys", "dict_values") and args:
                return args[0]
            if name == "dict" and args:
                return args[0]
            if name == "dict_items" and len(args) == 2:
                return TupleV(args)
            if name == "enumerate" and args:
                return TupleV((BuiltinV("int", True), args[0]))
            if name == "zip":
                return TupleV(args)
            if name == "str":
                return value
            if name == "range":
                return BuiltinV("int", True)
            return UNKNOWN
        if isinstance(value, ContainerV):
            return value.elem
        if isinstance(value, MappingV):
            return value.key
        if isinstance(value, TupleV):
            return union(list(value.items)) if value.items else UNKNOWN
        if isinstance(value, ClassV) and value.instance:
            method = self.find_member(value, "__iter__")
            if method is not None and method[0].kind == "def":
                return self.iter_elem(self.function_returns(method[1], method[0].bid, value))
        return UNKNOWN

    def apply_path(self, value: Value, path: tuple[Any, ...]) -> Value:
        for step in path:
            if step == "star":
                return UNKNOWN
            if isinstance(value, TupleV):
                try:
                    value = value.items[step]
                except IndexError:
                    return UNKNOWN
            elif isinstance(value, ContainerV):
                value = value.elem
            elif isinstance(value, BuiltinV) and value.instance and value.name == "list" and value.args:
                value = value.args[0]
            else:
                return UNKNOWN
        return value

    def enter(self, value: Value, is_async: bool) -> Value:
        value = strip_none(value)
        if not (isinstance(value, ClassV) and value.instance):
            return UNKNOWN
        hit = self.find_member(value, "__aenter__" if is_async else "__enter__")
        if hit is None or hit[0].kind != "def":
            return UNKNOWN
        result = self.function_returns(hit[1], hit[0].bid, value)
        if is_async:
            return result.inner if isinstance(result, AwaitableV) else UNKNOWN
        return result

    # classes -----------------------------------------------------------

    def mro(self, path: str, bid: int) -> tuple[list[tuple[str, int]], list[str], list[str], bool]:
        """``(project MRO, external base qualnames, decorators, complete)``.

        ``complete`` is False when some base could not be resolved at all —
        then "not found" must stay Unknown rather than become External.
        """
        key = (path, bid)
        cached = self._mro.get(key)
        if cached is not None:
            return cached
        marker = ("mro", key)
        if marker in self._busy:
            return [key], [], [], False
        self._busy.add(marker)
        try:
            result = self._compute_mro(path, bid)
        finally:
            self._busy.discard(marker)
        self._mro[key] = result
        return result

    def _compute_mro(self, path: str, bid: int) -> tuple[list[tuple[str, int]], list[str], list[str], bool]:
        hit = self.binding(path, bid)
        if hit is None or not isinstance(hit[1].node, ast.ClassDef):
            return [(path, bid)], [], [], False
        m, b = hit
        node = b.node
        assert isinstance(node, ast.ClassDef)
        scope = b.scope
        tscope = m.node_scope[id(node)].parent or scope
        externals: list[str] = []
        complete = True
        linearizations: list[list[tuple[str, int]]] = []
        direct: list[tuple[str, int]] = []
        for base in node.bases:
            target = base.value if isinstance(base, ast.Subscript) else base
            value = self.infer(m, target, tscope)
            if isinstance(value, ClassV) and not value.instance:
                sub, sub_ext, _decorators, sub_complete = self.mro(value.path, value.bid)
                linearizations.append(sub)
                direct.append((value.path, value.bid))
                externals.extend(e for e in sub_ext if e not in externals)
                complete = complete and sub_complete
            elif isinstance(value, ExternalV) and not value.instance:
                if value.qualname not in externals:
                    externals.append(value.qualname)
            elif isinstance(value, BuiltinV) and not value.instance:
                if value.name != "object":
                    externals.append(f"builtins.{value.name}")
            else:
                complete = False
        merged = _c3([*linearizations, direct])
        if merged is None:
            merged = []
            for linear in linearizations:
                merged.extend(c for c in linear if c not in merged)
        decorators = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            value = self.infer(m, target, scope)
            if isinstance(value, ExternalV):
                decorators.append(value.qualname)
            else:
                decorators.append(ast.unparse(target))
        return [(path, bid), *merged], externals, decorators, complete

    def class_members(self, path: str, bid: int, attr: str) -> tuple[Binding, str] | None:
        """A member defined directly on one class: body binding or ``self.attr``."""
        hit = self.binding(path, bid)
        if hit is None:
            return None
        m, _b = hit
        scope = m.class_scopes.get(bid)
        if scope is None:
            return None
        found = scope.bindings.get(attr)
        if found:
            defs = [f for f in found if f.kind in ("def", "class", "annassign", "assign")]
            if defs:
                # A property's canonical definition is its getter.
                return defs[0], path
        attrs = m.class_attrs.get(bid, {}).get(attr)
        if attrs:
            in_init = [a for a in attrs if a.extra.get("method") == "__init__"]
            return (in_init or attrs)[0], path
        return None

    def find_member(self, value: ClassV, attr: str, *, start: int = 0) -> tuple[Binding, str] | None:
        order, _ext, _dec, _complete = self.mro(value.path, value.bid)
        for path, bid in order[start:]:
            found = self.class_members(path, bid, attr)
            if found is not None:
                return found
        return None

    def class_is(self, value: ClassV, qualnames: frozenset[str]) -> bool:
        _order, externals, decorators, _complete = self.mro(value.path, value.bid)
        return any(e in qualnames for e in externals) or any(d in qualnames for d in decorators)

    def member(self, value: Value, attr: str) -> tuple[Value, Ref | None]:
        if self.depth > MAX_DEPTH:
            return UNKNOWN, None
        self.depth += 1
        try:
            return self._member(value, attr)
        except RecursionError:
            return UNKNOWN, None
        finally:
            self.depth -= 1

    def _member(self, value: Value, attr: str) -> tuple[Value, Ref | None]:
        value = strip_none(value)
        if isinstance(value, ModuleV):
            result, ref = self.module_member(value.path, attr)
            if ref is None:
                sub = self.resolve_module(f"{value.name}.{attr}", value.path)
                if sub is not None:
                    return ModuleV(sub, f"{value.name}.{attr}"), Ref("module", sub, name=f"{value.name}.{attr}")
            return result, ref
        if isinstance(value, (ClassV, SuperV)):
            if isinstance(value, SuperV):
                cls = ClassV(value.path, value.bid, instance=True)
                found = self.find_member(cls, attr, start=1)
            else:
                cls = value
                found = self.find_member(cls, attr)
            if found is not None:
                binding, path = found
                ref = Ref("project", path, binding.bid)
                return self.member_value(binding, path, cls), ref
            _order, externals, _dec, complete = self.mro(cls.path, cls.bid)
            externals = [e for e in externals if e not in IGNORED_EXTERNAL_BASES]
            if externals and complete:
                base = externals[0]
                if base.startswith("builtins."):
                    type_name = base.split(".", 1)[1]
                    if BuiltinCatalog.has_member(type_name, attr):
                        return UNKNOWN, Ref("builtin", name=f"{type_name}.{attr}")
                    return UNKNOWN, None
                return ExternalV(f"{base}.{attr}"), Ref("external", name=f"{base}.{attr}")
            return UNKNOWN, None
        if isinstance(value, BuiltinV):
            type_name = value.name if not value.instance else value.name
            if value.instance and type_name in ("dict_items", "dict_keys", "dict_values", "enumerate", "zip", "reversed"):
                return UNKNOWN, None
            if value.name == "None":
                return UNKNOWN, None
            if BuiltinCatalog.has_member(type_name, attr):
                ref = Ref("builtin", name=f"{type_name}.{attr}")
                if value.instance:
                    return BuiltinMethodV(type_name, attr, value), ref
                return UNKNOWN, ref
            return UNKNOWN, None
        if isinstance(value, ExternalV):
            return ExternalV(f"{value.qualname}.{attr}"), Ref("external", name=f"{value.qualname}.{attr}")
        if isinstance(value, UnionV):
            results = [self.member(item, attr) for item in value.items]
            refs = {r for _v, r in results}
            if len(refs) == 1 and None not in refs:
                return union([v for v, _r in results]), results[0][1]
            return UNKNOWN, None
        return UNKNOWN, None

    def member_value(self, binding: Binding, path: str, receiver: ClassV) -> Value:
        hit = self.binding(path, binding.bid)
        if hit is None:
            return UNKNOWN
        m, b = hit
        if b.kind == "def":
            decorators = _decorator_names(b.node)
            if any(d in PROPERTY_DECORATORS for d in decorators):
                return self.function_returns(path, b.bid, receiver) if receiver.instance else UNKNOWN
            if "staticmethod" in decorators:
                return FuncV(path, b.bid)
            return FuncV(path, b.bid, bound=receiver.instance or "classmethod" in decorators)
        if b.kind in ("assign", "annassign") and b.scope.kind == "class":
            owner = ClassV(path, b.scope.class_binding.bid) if b.scope.class_binding else None
            if owner is not None and b.annotation is None and self.class_is(owner, ENUM_BASES):
                return ClassV(owner.path, owner.bid, instance=True)
        return self.binding_value(path, b.bid)


@dataclass(frozen=True, slots=True)
class SelfV(Value):
    """``Self`` in a return annotation, bound to the receiver at the call."""

    path: str
    bid: int


@dataclass(frozen=True, slots=True)
class BuiltinMethodV(Value):
    type_name: str
    method: str
    receiver: BuiltinV


def _c3(sequences: list[list[tuple[str, int]]]) -> list[tuple[str, int]] | None:
    sequences = [list(s) for s in sequences if s]
    result: list[tuple[str, int]] = []
    while sequences:
        for seq in sequences:
            head = seq[0]
            if not any(head in other[1:] for other in sequences):
                break
        else:
            return None
        result.append(head)
        sequences = [[c for c in s if c != head] for s in sequences]
        sequences = [s for s in sequences if s]
    return result


def _is_typevar(binding: Binding) -> bool:
    value = binding.value
    return (
        binding.kind in ("assign", "typeparam")
        and (binding.kind == "typeparam" or (
            isinstance(value, ast.Call)
            and isinstance(value.func, (ast.Name, ast.Attribute))
            and (value.func.id if isinstance(value.func, ast.Name) else value.func.attr) in ("TypeVar", "ParamSpec", "TypeVarTuple")
        ))
    )


def _relocate(parsed: ast.expr, anchor: ast.expr) -> ast.expr:
    """Give a parsed string annotation positions inside its string literal."""
    for child in ast.walk(parsed):
        if hasattr(child, "lineno"):
            child.lineno = anchor.lineno  # type: ignore[attr-defined]
            child.col_offset = anchor.col_offset + 1 + child.col_offset  # type: ignore[attr-defined]
            child.end_lineno = anchor.lineno  # type: ignore[attr-defined]
            child.end_col_offset = anchor.col_offset + 1 + (child.end_col_offset or 0)  # type: ignore[attr-defined]
    return parsed


def _walk_body(node: ast.AST) -> Iterator[ast.AST]:
    """Walk a function body without entering nested functions or classes."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(child))


def _returns(node: ast.AST) -> list[ast.Return]:
    return [child for child in _walk_body(node) if isinstance(child, ast.Return)]


def _is_generator(node: ast.AST) -> bool:
    return any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in _walk_body(node))


# ── rendering signatures and hovers ──────────────────────────────────────


def _clip(text: str, limit: int = DEFAULT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "…"


def format_params(args: ast.arguments, *, drop_first: bool = False) -> str:
    out: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults))
    if drop_first and pairs:
        pairs = pairs[1:]
    posonly = len(args.posonlyargs) - (1 if drop_first and args.posonlyargs else 0)

    def one(arg: ast.arg, default: ast.expr | None, prefix: str = "") -> str:
        text = prefix + arg.arg
        if arg.annotation is not None:
            text += ": " + _unparse(arg.annotation)
        if default is not None:
            text += (" = " if arg.annotation is not None else "=") + _clip(_unparse(default))
        return text

    for index, (arg, default) in enumerate(pairs):
        out.append(one(arg, default))
        if posonly > 0 and index == posonly - 1:
            out.append("/")
    if args.vararg is not None:
        out.append(one(args.vararg, None, "*"))
    elif args.kwonlyargs:
        out.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        out.append(one(arg, default))
    if args.kwarg is not None:
        out.append(one(args.kwarg, None, "**"))
    return ", ".join(out)


def describe_value(value: Value, index: ProjectIndex) -> str | None:
    """A short type text for hovers; ``None`` when nothing is known."""
    value = value
    if isinstance(value, ClassV):
        hit = index.binding(value.path, value.bid)
        name = hit[1].name if hit else "?"
        return name if value.instance else f"type[{name}]"
    if isinstance(value, BuiltinV):
        if value.name == "None":
            return "None"
        if not value.instance:
            return None
        if value.name in ("list", "set", "frozenset") and value.args:
            inner = describe_value(value.args[0], index)
            return f"{value.name}[{inner}]" if inner else value.name
        if value.name == "dict" and len(value.args) == 2:
            key, val = (describe_value(v, index) for v in value.args)
            return f"dict[{key}, {val}]" if key and val else "dict"
        return value.name
    if isinstance(value, ContainerV):
        inner = describe_value(value.elem, index)
        return f"{value.kind}[{inner}]" if inner else value.kind
    if isinstance(value, MappingV):
        key, val = describe_value(value.key, index), describe_value(value.value, index)
        return f"{value.kind}[{key}, {val}]" if key and val else value.kind
    if isinstance(value, TupleV):
        parts = [describe_value(v, index) or "Unknown" for v in value.items]
        return f"tuple[{', '.join(parts)}]"
    if isinstance(value, ExternalV) and value.instance:
        return value.qualname
    if isinstance(value, UnionV):
        parts = [describe_value(v, index) for v in value.items]
        if all(parts):
            return " | ".join(p for p in parts if p)
    if isinstance(value, AwaitableV):
        inner = describe_value(value.inner, index)
        return f"Awaitable[{inner}]" if inner else None
    return None


def external_hint(name: str, *, via_import_module: bool, called: bool) -> int | None:
    last = name.rsplit(".", 1)[-1]
    if via_import_module:
        return T_MODULE
    if ALL_CAPS.match(last):
        return T_CONSTANT
    if CAP_WORDS.match(last):
        return T_CLASS
    if called:
        return T_FUNCTION
    return None


# ── emitting one file ────────────────────────────────────────────────────


@dataclass(slots=True)
class Emitted:
    tokens: dict[tuple[int, int], tuple[int, int, int, int]] = field(default_factory=dict)
    refs: dict[tuple[int, int], tuple[Any, ...]] = field(default_factory=dict)


class FileEmitter:
    """Classify every NAME token in one file and collect its symbols."""

    def __init__(self, index: ProjectIndex, model: FileModel) -> None:
        self.ix = index
        self.m = model
        self.out = Emitted()
        self.symbols: list[dict[str, Any]] = []
        self.symbol_ids: dict[tuple[Any, ...], int] = {}

    # tokens ------------------------------------------------------------

    def mark(
        self,
        span: tuple[int, int, int] | None,
        kind: int | None,
        mods: int = 0,
        symbol: tuple[Any, ...] | None = None,
        *,
        override: bool = False,
    ) -> None:
        if span is None or kind is None:
            return
        line, col, end = span
        key = (line, col)
        if key in self.out.tokens and not override:
            return
        self.out.tokens[key] = (line, col, end, kind | (mods << 8))
        if symbol is not None:
            self.out.refs[key] = symbol
        else:
            self.out.refs.pop(key, None)

    # symbols -----------------------------------------------------------

    def symbol(self, key: tuple[Any, ...], build: Any) -> int:
        existing = self.symbol_ids.get(key)
        if existing is not None:
            return existing
        payload = build()
        if payload is None:
            return -1
        self.symbol_ids[key] = len(self.symbols)
        self.symbols.append(payload)
        return self.symbol_ids[key]

    def project_symbol(self, ref: Ref) -> tuple[int | None, int, int]:
        """Return ``(symbol index, token type, modifiers)`` for a project ref."""
        if ref.origin == "module":
            module = self.ix.module_of.get(ref.path, ref.name)
            sid = self.symbol(("module", ref.path), lambda: self.module_symbol(ref.path, module))
            return (sid if sid >= 0 else None), T_MODULE, 0
        hit = self.ix.binding(ref.path, ref.bid)
        if hit is None:
            return None, T_VARIABLE, 0
        model, binding = hit
        kind, mods = self.binding_kind(model, binding)
        sid = self.symbol(("project", ref.path, ref.bid), lambda: self.binding_symbol(model, binding, kind))
        return (sid if sid >= 0 else None), kind, mods

    def binding_kind(self, model: FileModel, b: Binding) -> tuple[int, int]:
        mods = 0
        if b.kind == "def":
            node = b.node
            if isinstance(node, ast.AsyncFunctionDef):
                mods |= M_ASYNC
            decorators = _decorator_names(node)
            if any(d in PROPERTY_DECORATORS or d.endswith((".setter", ".deleter")) for d in decorators):
                return T_PROPERTY, mods
            if b.scope.kind == "class":
                return T_METHOD, mods
            return T_FUNCTION, mods
        if b.kind == "class":
            return T_CLASS, mods
        if b.kind == "param":
            if b.extra.get("method_of") is not None and b.extra.get("index") == 0 and b.name in ("self", "cls"):
                return T_SELF_PARAMETER, mods
            return T_PARAMETER, mods
        if b.kind == "typeparam" or _is_typevar(b):
            return T_TYPE_PARAMETER, mods
        if b.kind == "selfattr":
            return T_PROPERTY, mods
        if b.kind in ("assign", "annassign") and b.scope.kind == "class":
            value = self.ix.binding_value(model.path, b.bid)
            if isinstance(value, ClassV) and not value.instance:
                return T_CLASS, mods
            return (T_CONSTANT if ALL_CAPS.match(b.name) else T_PROPERTY), mods
        value = self.ix.binding_value(model.path, b.bid)
        if isinstance(value, ModuleV) or (isinstance(value, ExternalV) and value.via_import_module):
            return T_MODULE, mods
        if isinstance(value, ClassV) and not value.instance:
            return T_CLASS, mods
        if isinstance(value, FuncV):
            return T_FUNCTION, mods
        if ALL_CAPS.match(b.name):
            return T_CONSTANT, mods | M_READONLY
        return T_VARIABLE, mods

    def target_of(self, model: FileModel, b: Binding) -> dict[str, Any] | None:
        if b.span is None:
            return None
        line, col, end = b.span
        index = model.index
        return {
            "path": model.path,
            "line": line,
            "col": index.cp_to_u16(line, col),
            "end_col": index.cp_to_u16(line, end),
        }

    def module_symbol(self, path: str, module: str) -> dict[str, Any]:
        model = self.ix.model(path)
        doc = clean_doc(ast.get_docstring(model.tree)) if model and model.tree else None
        return {
            "kind": "module",
            "name": module.rsplit(".", 1)[-1],
            "qualname": module,
            "signature": f"(module) {module}",
            "type": None,
            "doc": doc,
            "target": {"path": path, "line": 1, "col": 0, "end_col": 0},
            "external": False,
        }

    def owner_name(self, model: FileModel, b: Binding) -> str | None:
        owner_bid = b.extra.get("owner")
        if owner_bid is None and b.scope.kind == "class" and b.scope.class_binding is not None:
            owner_bid = b.scope.class_binding.bid
        if owner_bid is None:
            return None
        return model.bindings[owner_bid].name

    def qualname(self, model: FileModel, b: Binding) -> str:
        parts = [b.name]
        scope: Scope | None = b.scope
        while scope is not None and scope.kind != "module":
            if scope.kind == "class" and scope.class_binding is not None:
                parts.insert(0, scope.class_binding.name)
            elif scope.kind == "function" and scope.node is not None:
                parts.insert(0, getattr(scope.node, "name", "<lambda>"))
            scope = scope.parent
        if b.kind == "selfattr":
            owner = self.owner_name(model, b)
            if owner:
                parts = [owner, b.name]
        return ".".join([p for p in [model.module, *parts] if p])

    def binding_symbol(self, model: FileModel, b: Binding, kind: int) -> dict[str, Any] | None:
        name = b.name
        doc: str | None = None
        type_text: str | None = None
        kind_name = KIND_NAMES.get(kind, "variable")
        if b.kind == "def":
            node = b.node
            assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            owner = self.owner_name(model, b)
            decorators = _decorator_names(node)
            if kind == T_PROPERTY:
                returns = _unparse(node.returns) if node.returns is not None else None
                type_text = returns
                label = f"{owner}.{name}" if owner else name
                signature = f"(property) {label}" + (f": {returns}" if returns else "")
            else:
                label = f"{owner}.{name}" if owner and kind == T_METHOD else name
                prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
                params = format_params(node.args)
                returns = f" -> {_unparse(node.returns)}" if node.returns is not None else ""
                signature = f"({kind_name}) {prefix}{label}({params}){returns}"
                shown = [d for d in decorators if d not in ("staticmethod",)]
                if decorators:
                    signature = "\n".join(f"@{d}" for d in decorators) + "\n" + signature
                del shown
            doc = clean_doc(ast.get_docstring(node))
        elif b.kind == "class":
            node = b.node
            assert isinstance(node, ast.ClassDef)
            signature = f"(class) {name}{self.constructor_signature(model, b)}"
            doc = clean_doc(ast.get_docstring(node))
            kind_name = "class"
        elif b.kind == "param":
            func = model.parents.get(id(b.node))
            arg = b.node
            assert isinstance(arg, ast.arg)
            annotation = _unparse(arg.annotation) if arg.annotation is not None else None
            if annotation is None:
                annotation = describe_value(self.ix.binding_value(model.path, b.bid), self.ix)
            role = b.extra.get("role")
            star = "*" if role == "vararg" else "**" if role == "kwarg" else ""
            type_text = annotation
            signature = f"(parameter) {star}{name}" + (f": {annotation}" if annotation else "")
            kind_name = "parameter"
            del func
        else:
            value = self.ix.binding_value(model.path, b.bid)
            if b.annotation is not None:
                type_text = _unparse(b.annotation)
            else:
                type_text = describe_value(value, self.ix)
            if kind == T_PROPERTY:
                owner = self.owner_name(model, b)
                label = f"{owner}.{name}" if owner else name
                signature = f"(property) {label}" + (f": {type_text}" if type_text else "")
            elif kind == T_CLASS:
                signature = f"(class) {name}"
            elif kind == T_MODULE:
                module = value.name if isinstance(value, ModuleV) else value.qualname if isinstance(value, ExternalV) else name
                signature = f"(module) {module}"
            elif kind == T_TYPE_PARAMETER:
                signature = f"(type parameter) {name}"
            else:
                label = "constant" if kind == T_CONSTANT else "variable"
                signature = f"({label}) {name}" + (f": {type_text}" if type_text else "")
                kind_name = label
        return {
            "kind": kind_name,
            "name": name,
            "qualname": self.qualname(model, b),
            "signature": signature,
            "type": type_text,
            "doc": doc,
            "target": self.target_of(model, b),
            "external": False,
        }

    def constructor_signature(self, model: FileModel, b: Binding) -> str:
        cls = ClassV(model.path, b.bid)
        init = self.ix.find_member(cls, "__init__")
        if init is not None and init[0].kind == "def":
            node = init[0].node
            assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            return f"({format_params(node.args, drop_first=True)})"
        fields = self.field_params(cls)
        if fields is not None:
            return "(" + ", ".join(fields) + ")"
        return "()"

    def field_params(self, cls: ClassV) -> list[str] | None:
        if not self.ix.class_is(cls, FIELD_CLASS_BASES | DATACLASS_DECORATORS):
            return None
        order, _ext, _dec, _complete = self.ix.mro(cls.path, cls.bid)
        seen: dict[str, str] = {}
        for path, bid in reversed(order):
            hit = self.ix.binding(path, bid)
            if hit is None or not isinstance(hit[1].node, ast.ClassDef):
                continue
            for stmt in hit[1].node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id.startswith("_"):
                        # Pydantic private attributes are not constructor fields.
                        continue
                    if typing_short(self.ix.infer(hit[0], stmt.annotation.value if isinstance(stmt.annotation, ast.Subscript) else stmt.annotation, hit[0].module_scope)) == "ClassVar":
                        continue
                    text = f"{stmt.target.id}: {_unparse(stmt.annotation)}"
                    if stmt.value is not None:
                        text += f" = {_clip(_unparse(stmt.value))}"
                    seen[stmt.target.id] = text
        return list(seen.values())

    def field_binding(self, cls: ClassV, name: str) -> tuple[Binding, str] | None:
        if not self.ix.class_is(cls, FIELD_CLASS_BASES | DATACLASS_DECORATORS):
            return None
        found = self.ix.find_member(cls, name)
        if found is not None and found[0].kind == "annassign":
            return found
        return None

    def builtin_symbol(self, dotted: str) -> int:
        def build() -> dict[str, Any]:
            signature, doc = self.ix.builtins.describe(dotted)
            return {
                "kind": "class" if signature.startswith("(class)") else "function",
                "name": dotted.rsplit(".", 1)[-1],
                "qualname": f"builtins.{dotted}",
                "signature": signature,
                "type": None,
                "doc": doc,
                "target": None,
                "external": True,
                "origin": "builtin",
                "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
            }
        return self.symbol(("builtin", dotted), build)

    def external_symbol(self, qualname: str, kind: int | None) -> int:
        label = {T_MODULE: "module", T_CLASS: "class", T_FUNCTION: "function", T_CONSTANT: "variable"}.get(kind or -1, "name")

        def build() -> dict[str, Any]:
            return {
                "kind": label if label != "name" else "variable",
                "name": qualname.rsplit(".", 1)[-1],
                "qualname": qualname,
                "signature": f"({label}) {qualname}",
                "type": None,
                "doc": None,
                "target": None,
                "external": True,
                "origin": "external",
            }
        return self.symbol(("external", qualname, label), build)

    # the main pass -----------------------------------------------------

    def ref_token(self, span: tuple[int, int, int] | None, ref: Ref | None, value: Value, *, called: bool, declaration: bool = False) -> None:
        if span is None:
            return
        name = self.m.lexed.names.get(span[:2], ("", 0))[0]
        if name in CONSTANT_NAMES:
            return
        decl = M_DECLARATION if declaration else 0
        if ref is None:
            if called:
                self.mark(span, T_FUNCTION, decl)
            return
        if ref.origin in ("project", "module"):
            sid, kind, mods = self.project_symbol(ref)
            if ref.origin == "project":
                hit = self.ix.binding(ref.path, ref.bid)
                if hit is not None and hit[1].kind in ("assign", "annassign") and hit[1].scope.kind != "class":
                    # A module-level alias of a class or function reads as what it names.
                    pass
            self.mark(span, kind, mods | decl, (sid,) if sid is not None else None)
            return
        if ref.origin == "builtin":
            dotted = ref.name
            is_class = "." not in dotted and BuiltinCatalog.is_class(dotted)
            kind = T_CLASS if is_class else (T_METHOD if "." in dotted else T_FUNCTION)
            if "." not in dotted and not callable(getattr(builtins, dotted, None)):
                kind = T_CONSTANT if ALL_CAPS.match(dotted) else T_VARIABLE
            sid = self.builtin_symbol(dotted)
            self.mark(span, kind, M_BUILTIN | M_EXTERNAL | decl, (sid,))
            return
        via_module = isinstance(value, ExternalV) and value.via_import_module or ref.hint == "module"
        kind = external_hint(ref.name, via_import_module=via_module, called=called)
        sid = self.external_symbol(ref.name, kind)
        self.mark(span, kind if kind is not None else T_VARIABLE, M_EXTERNAL | decl, (sid,))
        if kind is None:
            # Uncoloured but hoverable: drop the colour, keep the reference.
            line, col, end = span
            self.out.tokens[(line, col)] = (line, col, end, -1 | (0 << 8))

    def is_called(self, node: ast.AST) -> bool:
        parent = self.m.parents.get(id(node))
        return isinstance(parent, ast.Call) and parent.func is node

    def run(self) -> None:
        m = self.m
        if m.tree is None:
            return
        for span in m.soft_keywords:
            self.mark(span, T_KEYWORD)
        # Definitions first, so later passes never override them.
        for b in m.bindings:
            if b.span is None or b.kind in ("import", "importfrom"):
                continue
            ref = Ref("project", m.path, b.bid)
            sid, kind, mods = self.project_symbol(ref)
            self.mark(b.span, kind, mods | M_DECLARATION, (sid,) if sid is not None else None)
        # Imports: module path segments and imported names.
        for span, dotted, level, _x in m.import_modules:
            base = self.ix.relative_base(m.path, level, dotted) if level else dotted
            value = self.ix.module_value(base, m.path) if base else UNKNOWN
            if isinstance(value, ModuleV):
                self.ref_token(span, Ref("module", value.path, name=base), value, called=False)
            elif isinstance(value, ExternalV):
                self.ref_token(span, Ref("external", name=base or dotted, hint="module"), value, called=False)
            else:
                self.mark(span, T_MODULE)
        for span, binding, module, level, name in m.import_names:
            if binding is None:
                continue
            ref = self.ix.binding_ref(m.path, binding)
            value = self.ix.binding_value(m.path, binding.bid)
            if level == -1:
                # `import a.b as c`: the alias names the module.
                self.ref_token(span, ref, value, called=False)
                continue
            self.ref_token(span, ref, value, called=False)
        for node, scope in m.uses:
            span = m.span_at(*m.pos(node), node.id)
            if span is None:
                continue
            value, ref = self.ix.name_value(m, node, scope)
            if ref is not None and ref.origin == "project" and ref.path == m.path:
                target = m.bindings[ref.bid] if 0 <= ref.bid < len(m.bindings) else None
                if target is not None and target.span == span:
                    continue
            self.ref_token(span, ref, value, called=self.is_called(node))
        for node, scope in m.attrs:
            span = m.attr_span(node)
            if span is None:
                continue
            receiver = self.ix.infer(m, node.value, scope)
            value, ref = self.ix.member(receiver, node.attr)
            if ref is not None and ref.origin == "project":
                hit = self.ix.binding(ref.path, ref.bid)
                if hit is not None and hit[1].span == span and ref.path == m.path:
                    continue
            if ref is not None and ref.origin == "builtin" and isinstance(strip_none(receiver), BuiltinV):
                receiver_value = strip_none(receiver)
                assert isinstance(receiver_value, BuiltinV)
                if not receiver_value.instance and "." not in ref.name:
                    pass
            self.ref_token(span, ref, value, called=self.is_called(node))
        for node, scope in m.calls:
            func = self.ix.infer(m, node.func, scope)
            for keyword_node in node.keywords:
                if keyword_node.arg is None:
                    continue
                start = m.pos(keyword_node)
                span = m.span_at(*start, keyword_node.arg)
                if span is None:
                    continue
                target = self.keyword_target(func, keyword_node.arg)
                if target is not None:
                    self.ref_token(span, target, UNKNOWN, called=False)
                else:
                    self.mark(span, T_PARAMETER)
        for node, scope, names in m.declared:
            for name in names:
                span = m.find_name(name, m.pos(node), m.end(node))
                owner: Scope | None = m.module_scope
                if isinstance(node, ast.Nonlocal):
                    owner = scope.parent
                    while owner is not None and (owner.kind == "class" or name not in owner.bindings):
                        owner = owner.parent
                found = owner.bindings.get(name) if owner is not None else None
                if found:
                    # The declaration names the variable, i.e. its first binding.
                    self.ref_token(span, Ref("project", m.path, found[0].bid), UNKNOWN, called=False)

    def keyword_target(self, func: Value, name: str) -> Ref | None:
        func = strip_none(func)
        node: ast.AST | None = None
        path = ""
        if isinstance(func, FuncV):
            hit = self.ix.binding(func.path, func.bid)
            if hit is not None:
                node, path = hit[1].node, func.path
        elif isinstance(func, ClassV) and not func.instance:
            init = self.ix.find_member(func, "__init__")
            if init is not None and init[0].kind == "def":
                node, path = init[0].node, init[1]
            else:
                field_hit = self.field_binding(func, name)
                if field_hit is not None:
                    return Ref("project", field_hit[1], field_hit[0].bid)
                return None
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        model = self.ix.model(path)
        if model is None:
            return None
        fscope = model.node_scope.get(id(node))
        if fscope is None:
            return None
        for b in fscope.bindings.get(name, []):
            if b.kind == "param" and b.extra.get("role") in ("positional", "kwonly"):
                return Ref("project", path, b.bid)
        return None

    # payload -----------------------------------------------------------

    def payload(self, sha256: str, syntax_error: dict[str, Any] | None) -> dict[str, Any]:
        m = self.m
        index = m.index
        spans: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        for line, start, end, kind in m.lexed.segments:
            spans.setdefault((line, start), (line, start, end, kind))
        for key, (line, col, end, packed) in self.out.tokens.items():
            spans[key] = (line, col, end, packed)
        # Keywords and literal constants that nothing else claimed.
        stop = m.lexed.stopped_at
        for (line, col), (string, end) in m.lexed.names.items():
            if (line, col) in spans:
                continue
            if stop is not None and (line, col) >= stop:
                continue
            if string in CONSTANT_NAMES:
                spans[(line, col)] = (line, col, end, T_CONSTANT)
            elif keyword.iskeyword(string):
                spans[(line, col)] = (line, col, end, T_KEYWORD)
        ordered = sorted(spans.values(), key=lambda item: (item[0], item[1]))
        tokens: list[int] = []
        refs: list[int] = []
        prev_line, prev_col = 1, 0
        last_end = (0, 0)
        count = 0
        for line, start, end, packed in ordered:
            if (line, start) < last_end:
                continue  # overlapping lexical span (f-string holes already split)
            kind = packed & 0xFF if packed >= 0 else 0xFF
            mods = (packed >> 8) if packed >= 0 else 0
            ref = self.out.refs.get((line, start))
            if kind == 0xFF and ref is None:
                continue
            u_start = index.cp_to_u16(line, start)
            u_end = index.cp_to_u16(line, end)
            if u_end <= u_start:
                continue
            delta_line = line - prev_line
            delta_col = u_start - (prev_col if delta_line == 0 else 0)
            tokens.extend((delta_line, delta_col, u_end - u_start, kind if kind != 0xFF else -1, mods))
            if ref is not None and ref[0] is not None:
                refs.extend((count, ref[0]))
            prev_line, prev_col = line, u_start
            last_end = (line, end)
            count += 1
        return {
            "v": RESULT_FORMAT,
            "path": m.path,
            "sha256": sha256,
            "syntax_error": syntax_error,
            "tokens": tokens,
            "refs": refs,
            "symbols": self.symbols,
        }


def lexical_payload(path: str, text: str, sha256: str, syntax_error: dict[str, Any] | None) -> dict[str, Any]:
    model = FileModel(path, "", text, None, lex(text))
    return FileEmitter(ProjectIndex({}), model).payload(sha256, syntax_error)


def syntax_error_of(text: str, index: LineIndex) -> dict[str, Any] | None:
    try:
        ast.parse(text)
    except SyntaxError as exc:
        line = exc.lineno or 1
        col = max(0, (exc.offset or 1) - 1)
        return {"line": line, "col": index.cp_to_u16(line, col), "msg": exc.msg or "invalid syntax"}
    except (ValueError, RecursionError) as exc:
        return {"line": 1, "col": 0, "msg": str(exc)}
    return None


def analyze_project(texts: dict[str, str], paths: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Analyse ``paths`` (default: all) against the whole project.

    The pure entry point tests use. ``texts`` maps project-relative paths to
    newline-normalised source.
    """
    index = ProjectIndex(texts)
    results: dict[str, dict[str, Any]] = {}
    for path in paths if paths is not None else sorted(texts):
        results[path] = analyze_file(index, path)
    return results


def analyze_file(index: ProjectIndex, path: str, sha256: str = "") -> dict[str, Any]:
    text = index.texts[path]
    model = index.model(path)
    assert model is not None
    error = syntax_error_of(text, model.index) if model.tree is None else None
    emitter = FileEmitter(index, model)
    try:
        emitter.run()
    except RecursionError:
        emitter = FileEmitter(index, model)
    return emitter.payload(sha256, error)


# ── the subprocess: incremental job runner ───────────────────────────────


def cache_file_for(cache_dir: Path, path: str) -> Path:
    digest = hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()
    return cache_dir / "files" / digest[:2] / f"{digest}.json.gz"


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_json_gz(path: Path) -> Any:
    try:
        return json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, ValueError, EOFError):
        return None


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class PriorityFeed:
    """Paths to analyse next, fed by ``{"prioritize": [...]}`` stdin lines."""

    def __init__(self, stream: Any | None) -> None:
        self._queue: "queue.Queue[str]" = queue.Queue()
        if stream is not None:
            threading.Thread(target=self._read, args=(stream,), daemon=True).start()

    def _read(self, stream: Any) -> None:
        for line in stream:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            for path in message.get("prioritize", []) if isinstance(message, dict) else []:
                if isinstance(path, str):
                    self._queue.put(path)

    def drain(self) -> list[str]:
        paths = []
        while True:
            try:
                paths.append(self._queue.get_nowait())
            except queue.Empty:
                return paths


def run_job(job: dict[str, Any], feed: PriorityFeed | None = None, emit: Any = _emit) -> dict[str, Any]:
    """Phase 1 then phase 2 for one project, writing results into the cache.

    ``job["reuse"]`` is the previous manifest (``path -> sha256``); files
    whose bytes still hash the same keep their cached result unless some
    summary changed.
    """
    root = Path(job["root"])
    cache_dir = Path(job["cache_dir"])
    entries = job.get("files", [])
    reuse: dict[str, str] = job.get("reuse", {})
    feed = feed or PriorityFeed(None)
    previous = _read_json_gz(cache_dir / "summaries.json.gz") or {}
    if not isinstance(previous, dict):
        previous = {}
    file_set = {entry["path"] for entry in entries}

    # Phase 1: read, hash, summarise.
    texts: dict[str, str] = {}
    hashes: dict[str, str] = {}
    digests: dict[str, str] = {}
    manifest: dict[str, dict[str, Any]] = {}
    failed = 0
    total = len(entries)
    for done, entry in enumerate(entries, 1):
        path = entry["path"]
        try:
            raw = (root / path).read_bytes()
        except OSError:
            failed += 1
            continue
        sha = hashlib.sha256(raw).hexdigest()
        manifest[path] = {"mtime_ns": entry.get("mtime_ns"), "size": entry.get("size"), "sha256": sha}
        hashes[path] = sha
        if len(raw) > ANALYSIS_CAP:
            continue
        text = decode_source(raw, python=True)
        texts[path] = text
        module, _root = module_name_for(path, file_set)
        cached = previous.get(path)
        if isinstance(cached, dict) and cached.get("sha256") == sha and cached.get("module") == module:
            digests[path] = cached["digest"]
        else:
            try:
                tree = ast.parse(text, filename=path)
                digests[path] = summary_digest(tree, module)
            except (SyntaxError, ValueError, RecursionError):
                digests[path] = "syntax-error:" + sha
        if done % 25 == 0 or done == total:
            emit({"event": "progress", "phase": "parse", "done": done, "total": total})

    old_digests = {path: value.get("digest") for path, value in previous.items() if isinstance(value, dict)}
    summaries_changed = old_digests != digests
    changed = [
        path for path in texts
        if reuse.get(path) != hashes.get(path) or not cache_file_for(cache_dir, path).exists()
    ]
    todo = sorted(texts) if summaries_changed else sorted(changed)

    # Stale results of files that left the project.
    for path in set(reuse) - set(hashes):
        cache_file_for(cache_dir, path).unlink(missing_ok=True)
    for path in set(hashes) - set(texts):
        cache_file_for(cache_dir, path).unlink(missing_ok=True)

    # Phase 2: resolve, highest priority first.
    index = ProjectIndex(texts)
    pending = list(todo)
    pending_set = set(pending)
    front: list[str] = [p for p in job.get("priority", []) if p in pending_set]
    total = len(pending)
    done = 0
    emit({"event": "progress", "phase": "resolve", "done": 0, "total": total})
    while pending_set:
        front.extend(p for p in feed.drain() if p in pending_set)
        path = None
        while front:
            candidate = front.pop(0)
            if candidate in pending_set:
                path = candidate
                break
        if path is None:
            path = next(p for p in pending if p in pending_set)
        pending_set.discard(path)
        try:
            payload = analyze_file(index, path, hashes[path])
        except Exception as exc:  # noqa: BLE001 — one bad file must not sink the run
            print(f"analysis failed for {path}: {exc!r}", file=sys.stderr)
            payload = lexical_payload(path, texts[path], hashes[path], None)
        # mtime=0: an unchanged result keeps its bytes, and so its ETag.
        data = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "surrogatepass"), 6, mtime=0)
        write_atomic(cache_file_for(cache_dir, path), data)
        done += 1
        emit({"event": "file", "path": path, "ok": True})
        emit({"event": "progress", "phase": "resolve", "done": done, "total": total})

    summaries = {
        path: {"sha256": hashes[path], "digest": digests[path], "module": module_name_for(path, file_set)[0]}
        for path in texts
    }
    write_atomic(cache_dir / "summaries.json.gz", gzip.compress(json.dumps(summaries).encode(), 6))
    write_atomic(cache_dir / "manifest.json", json.dumps(manifest).encode())
    result = {"event": "done", "failed": failed, "analyzed": done, "files": len(manifest)}
    emit(result)
    return result


def main() -> int:
    line = sys.stdin.readline()
    if not line:
        return 2
    job = json.loads(line)
    run_job(job, PriorityFeed(sys.stdin))
    return 0


if __name__ == "__main__":
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4000))
    raise SystemExit(main())
