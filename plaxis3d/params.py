"""
plaxis3d.params
===============

Load a ``*.params`` file (INI-style, stdlib :mod:`configparser` — no external
dependencies, so it runs under Plaxis' bundled Python) into an ordered list of
sections that :mod:`plaxis3d.runner` turns into Plaxis commands.

Value syntax
------------
* numbers      ``-60``, ``15.65``, ``5E-5``        -> int / float
* expressions  ``-2 * $k``, ``($k + 1) / 2``        -> evaluated number
* variables    ``$k`` (defined in a ``[variables]`` section)
* booleans     ``true`` / ``false`` (any case)      -> bool
* nothing      ``none``                             -> None   (empty = "" string)
* text         ``Hardening Soil``                   -> str (quotes optional)
* point lists  ``(-$k, $k, 0), (5,5,0)``            -> [(-50, 50, 0), (5,5,0)]
* value lists  ``EmbeddedBeams, Geogrids``          -> [...]  (for list keys)

Variables
---------
Define reusable values in a ``[variables]`` section, one per line with a ``$``
prefix; reference them (and combine them with arithmetic) in any later value::

    [variables]
    $k = 50
    $depth = -2 * $k          # variables may reference earlier ones

    [soil_contour]
    x_min = -$k               # -> -50
    x_max = $k                # ->  50

Expressions are evaluated with a restricted arithmetic evaluator
(``+ - * / // % **`` and parentheses only — no function calls, names, or
attribute access), so a value like ``Hardening Soil`` is never "evaluated" and
stays a plain string. Variables are resolved per file, before any
defaults/override merge.

Comments start with ``#`` (full line or inline). Section headers may carry a
name after a colon, e.g. ``[soil_material:kum]`` -> kind ``soil_material``,
name ``kum``. Section order in the file is preserved and honoured by the
builder, so define materials before the structures that reference them.
"""

from __future__ import annotations

import ast
import configparser
import itertools
import operator
import re
from collections import namedtuple

# one parsed section: kind (e.g. "soil_material"), optional name, params dict
Section = namedtuple("Section", "kind name params")

# one build produced from a file: a label (e.g. "bh50_gu20"), the loop values
# that produced it, and the fully-resolved sections for that combination.
ParamSet = namedtuple("ParamSet", "label loop_vars sections")

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}

# keys whose (comma-separated) value is always a list of scalars
LIST_KEYS = {"layers", "activate", "deactivate", "refine", "soil_block"}

# ``$name`` variable references
_VAR_RE = re.compile(r"\$([A-Za-z_]\w*)")

# operators allowed in value expressions (no names / calls / attributes)
_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval_expr(expr: str):
    """Safely evaluate an arithmetic expression; raise on anything else."""
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")
    return _eval(ast.parse(expr, mode="eval").body)


def _substitute(text: str, variables: dict) -> str:
    """Replace ``$name`` with the variable's value; undefined names raise."""
    def repl(match):
        name = match.group(1)
        if name not in variables:
            raise KeyError(f"undefined variable ${name}")
        return repr(variables[name])
    return _VAR_RE.sub(repl, text)


def _parse_scalar(text: str):
    """Convert a single token to bool / None / number(expr) / str."""
    s = text.strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]  # explicitly quoted -> keep as literal string
    low = s.lower()
    if low == "none":
        return None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return _eval_expr(s)   # numbers and arithmetic expressions
    except Exception:
        return s               # anything else is plain text


def _parse_tuples(text: str) -> list:
    """'(-5,5,0), (5,5,0)' -> [(-5.0, 5.0, 0.0), (5.0, 5.0, 0.0)]"""
    out = []
    for group in re.findall(r"\(([^)]*)\)", text):
        nums = [_parse_scalar(x) for x in group.split(",") if x.strip() != ""]
        out.append(tuple(nums))
    return out


def parse_value(key: str, raw: str, variables: dict | None = None):
    """Parse one option value: substitute ``$variables``, then type it.

    Typing order matters: quoted text stays literal, list keys stay lists, and
    an arithmetic expression is tried *before* the point-list branch — so a
    parenthesised expression like ``5677 * ($D / 0.8) ** 2`` evaluates to a
    number instead of being mistaken for ``(x, y, z)`` tuples (a tuple never
    parses as arithmetic, so real corner lists still reach ``_parse_tuples``).
    """
    s = raw.strip()
    if variables:
        s = _substitute(s, variables)
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]  # explicitly quoted -> keep as literal string
    if key in LIST_KEYS:
        return [_parse_scalar(x) for x in s.split(",") if x.strip() != ""]
    try:
        return _eval_expr(s)
    except Exception:
        pass
    if "(" in s and ")" in s:
        return _parse_tuples(s)
    return _parse_scalar(s)


# sections that configure parsing, not the model (never emitted as builds)
_META_SECTIONS = {"variables", "loop"}


def _meta_items(cp: configparser.ConfigParser, section: str):
    """Yield ``(key, raw)`` for each entry of a meta section (``[variables]`` /
    ``[loop]``), matching the section name case-insensitively. Keys keep their
    ``$`` prefix — callers strip it (loop keys may name several variables)."""
    for name in cp.sections():
        if name.strip().lower() != section:
            continue
        yield from cp.items(name)


def _resolve_variables(cp: configparser.ConfigParser,
                       initial: dict | None = None) -> dict:
    """Build the variable table from a ``[variables]`` section (if present).

    ``initial`` seeds the table (used to inject the current loop values), and
    variables are resolved in order, so a variable may reference earlier ones —
    or the injected loop values.
    """
    variables: dict = dict(initial or {})
    for key, raw in _meta_items(cp, "variables"):
        var = key[1:] if key.startswith("$") else key
        variables[var] = parse_value(var, raw, variables)
    return variables


def _extract_loop(cp: configparser.ConfigParser) -> list:
    """Parse a ``[loop]`` section into ``[(names, [value-tuples]), ...]`` in order.

    Each line is one loop *dimension*. Its key names one variable — or several,
    comma-separated, that vary **together** (zipped, not crossed)::

        $mult = {0.5, 1.0, 1.5, 2.0}                # one variable
        $clay, $silt = {(11, 13), (5, 5), (5, 20)}  # paired values

    Dimensions are crossed with each other (Cartesian product); the names within
    one line advance in lockstep, so the pairs stay together. Values may be
    numbers or arithmetic expressions. ``names`` is always a tuple and every
    value is a tuple of the same arity. Returns ``[]`` when there is no
    ``[loop]`` section.
    """
    loop: list = []
    for key, raw in _meta_items(cp, "loop"):
        names = tuple(k.strip().lstrip("$") for k in key.split(","))
        values = _parse_set(raw, arity=len(names))
        loop.append((names, values))
    return loop


def _parse_set(raw: str, arity: int = 1) -> list:
    """'{50, 60, 70}' -> [(50,), (60,), (70,)]  (braces optional).

    With ``arity > 1`` the values must be tuples of that size, e.g.
    '{(11, 13), (5, 5)}' -> [(11, 13), (5, 5)]. Scalars are wrapped so every
    entry is a tuple, matching the (possibly multi-) names of its loop line.
    """
    s = raw.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    if "(" in s:
        values = _parse_tuples(s)
    else:
        values = [(_parse_scalar(x),) for x in s.split(",") if x.strip() != ""]
    for value in values:
        if len(value) != arity:
            raise ValueError(
                f"loop value {value!r} has {len(value)} element(s), "
                f"expected {arity} (one per variable on its line)")
    return values


def _fmt(value) -> str:
    """Format a loop value for a name/label (50 -> '50', 17.5 -> '17.5')."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _build_sections(cp: configparser.ConfigParser, variables: dict) -> list:
    """Turn every non-meta section into a parsed :class:`Section`, in order."""
    sections: list = []
    for name in cp.sections():
        if name.strip().lower() in _META_SECTIONS:
            continue
        if ":" in name:
            kind, obj_name = name.split(":", 1)
            kind, obj_name = kind.strip(), obj_name.strip()
        else:
            kind, obj_name = name.strip(), None
        params = {k: parse_value(k, v, variables) for k, v in cp.items(name)}
        sections.append(Section(kind, obj_name, params))
    return sections


def _read_cp(path: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(
        interpolation=None,               # values may contain '%', '$', etc.
        delimiters=("=",),                # only '=' splits key/value
        comment_prefixes=("#", ";"),
        inline_comment_prefixes=("#",),
    )
    cp.optionxform = str                  # preserve case (E50Ref, K0NC, sigz …)
    with open(path, encoding="utf-8-sig") as fh:   # utf-8-sig tolerates a BOM
        cp.read_file(fh)
    return cp


def load_params(path: str) -> list[Section]:
    """Read a ``.params`` file and return its sections, in file order.

    ``[variables]`` / ``[loop]`` sections are consumed and never returned. If
    the file defines a ``[loop]`` only its first combination is returned; use
    :func:`load_param_sets` to expand a loop.
    """
    return load_param_sets(path)[0].sections


def load_param_sets(path: str) -> list[ParamSet]:
    """Expand a ``.params`` file into one :class:`ParamSet` per loop combination.

    Without a ``[loop]`` section this returns a single set (``label=""``). With
    one, it returns the Cartesian product of the loop *lines*, each with its own
    variable table and a label like ``bh50_gu20``. A line naming several
    variables (``$clay, $silt = {(11, 13), (5, 5)}``) advances them together —
    its pairs are one dimension of the product, not two.
    """
    cp = _read_cp(path)
    loop = _extract_loop(cp)

    if not loop:
        variables = _resolve_variables(cp)
        return [ParamSet("", {}, _build_sections(cp, variables))]

    name_groups = [names for names, _ in loop]        # one tuple per loop line
    value_lists = [vals for _, vals in loop]
    sets: list[ParamSet] = []
    for combo in itertools.product(*value_lists):
        # combo holds one value-tuple per loop line; zip each with its names
        injected = {n: v for names, values in zip(name_groups, combo)
                    for n, v in zip(names, values)}
        variables = _resolve_variables(cp, injected)
        sections = _build_sections(cp, variables)
        # names starting with '_' are data riding along a zipped line (e.g. a
        # pile-capacity table keyed by the visible mult/D): usable as variables
        # but kept out of the label so the model names stay readable.
        label = "_".join(f"{n}{_fmt(v)}" for n, v in injected.items()
                         if not n.startswith("_"))
        sets.append(ParamSet(label, injected, sections))
    return sets


def merge_sections(base: list[Section], override: list[Section]) -> list[Section]:
    """Overlay ``override`` on ``base``, deep-merging by (kind, name).

    * A section present in both keeps ``base``'s keys and updates them with
      ``override``'s keys (so an override only needs the keys it changes).
    * A section only in ``base`` is kept as-is.
    * A section only in ``override`` is inserted **next to its own kind** — right
      after the last existing section of the same kind (so a new
      ``[soil_material:kil]`` lands among the materials, before the ``[layer]``
      and structures that use it, and a new ``[phase:3]`` after the last phase).
      If the kind is new to the model, it is appended at the end. New sections of
      the same kind keep their override order.

    ``base`` order is otherwise preserved.
    """
    merged: list[Section] = [Section(s.kind, s.name, dict(s.params)) for s in base]
    keys = {(s.kind, s.name): i for i, s in enumerate(merged)}

    for sec in override:
        key = (sec.kind, sec.name)
        if key in keys:
            merged[keys[key]].params.update(sec.params)
            continue
        new_sec = Section(sec.kind, sec.name, dict(sec.params))
        last = _last_index_of_kind(merged, sec.kind)
        if last is None:
            merged.append(new_sec)
        else:
            merged.insert(last + 1, new_sec)
        keys = {(s.kind, s.name): i for i, s in enumerate(merged)}  # indices shifted
    return merged


def _last_index_of_kind(sections: list[Section], kind: str) -> int | None:
    """Index of the last section of ``kind`` in ``sections`` (or None)."""
    last = None
    for i, sec in enumerate(sections):
        if sec.kind == kind:
            last = i
    return last
