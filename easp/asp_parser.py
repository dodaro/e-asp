"""Small text-level helpers to inspect ASP rules.

This module replaces the ANTLR-based ``Reader`` of the Java implementation.
It never builds a full syntax tree: every function works directly on the rule
text, taking care of quoted strings and nested parentheses/braces so that
commas or symbols inside terms are not misinterpreted.
"""

from __future__ import annotations

import re
from collections import OrderedDict


# An ASP variable starts with an uppercase letter (the anonymous variable "_"
# is intentionally NOT matched: it can never be reused outside its literal).
_VARIABLE_TOKEN = re.compile(r"\b[A-Z][A-Za-z0-9_]*")


def split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split ``text`` on ``separator`` ignoring separators nested inside
    quotes, parentheses, braces, or brackets.

    Example: ``split_top_level('f(X,Y), "a,b", Z')`` -> ``['f(X,Y)', '"a,b"', 'Z']``.
    """
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for i, char in enumerate(text):
        if quote:
            if char == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def strip_final_dot(rule: str) -> str:
    """Remove the trailing '.' of a rule, if present."""
    rule = rule.strip()
    return rule[:-1].strip() if rule.endswith(".") else rule


def strip_line_comment(line: str) -> str:
    """Remove an inline ``% comment`` (quote-aware, so a '%' inside a string
    constant is preserved)."""
    quote: str | None = None
    for i, char in enumerate(line):
        if quote:
            if char == quote and line[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "%":
            return line[:i].rstrip()
    return line


def _normalize_spaces(text: str) -> str:
    """Collapse runs of whitespace into single spaces (must NOT delete them:
    ``not a`` would otherwise become the different atom ``nota``)."""
    return " ".join(text.split())


def normalize_program(program: str) -> str:
    """Rewrite a program so that every statement sits on exactly one line.

    The debugger pipeline is line-based, so rules spanning several lines
    would otherwise be instrumented incorrectly. Comment lines are dropped
    (inline comments too). Statement terminators are detected quote-aware;
    ``..`` intervals such as ``a(1..4)`` are never split, and the weight
    block of a weak constraint (``:~ body. [w@l]``) stays attached to its
    statement. Statements already on one line keep their original spacing,
    so rule texts shown to the user are unchanged."""
    stripped_lines = []
    for raw_line in program.splitlines():
        line = strip_line_comment(raw_line)
        if line.strip().startswith("%"):
            continue
        stripped_lines.append(line)
    text = "\n".join(stripped_lines)

    statements: list[str] = []

    def emit(fragment: str) -> None:
        statement = re.sub(r"\s*\n\s*", " ", fragment).strip()
        if statement:
            statements.append(statement)

    def absorb_marker(pos: int) -> int:
        """Keep a user ``@ignore``/``@correct`` annotation written after the
        terminator attached to its statement."""
        j = pos + 1
        while j < len(text) and text[j] in " \t":
            j += 1
        for marker in ("@ignore", "@correct"):
            if text.startswith(marker, j):
                return j + len(marker) - 1
        return pos

    start = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == ".":
            # ".." is an interval, not a terminator.
            if (i > 0 and text[i - 1] == ".") or (i + 1 < len(text) and text[i + 1] == "."):
                i += 1
                continue
            end = i
            # A weak constraint continues with its "[cost@level]" block.
            j = i + 1
            while j < len(text) and text[j] in " \t\n":
                j += 1
            if j < len(text) and text[j] == "[":
                bracket_end = _matching_bracket(text, j)
                if bracket_end > 0:
                    end = bracket_end
            end = absorb_marker(end)
            emit(text[start : end + 1])
            start = i = end + 1
            continue
        i += 1
    emit(text[start:])
    return "\n".join(statements) + ("\n" if statements else "")


def _matching_bracket(text: str, start: int) -> int:
    """Index of the ']' matching the '[' at ``start`` (quote-aware)."""
    depth = 0
    quote: str | None = None
    for i in range(start, len(text)):
        char = text[i]
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def variables_of(text: str) -> list[str]:
    """Return the variable names appearing in a term or literal, in order of
    appearance and without duplicates.

    Content of quoted strings is ignored, so ``p("X",Y+1)`` yields ``['Y']``.
    The anonymous variable ``_`` is never returned because each occurrence
    stands for a fresh variable and cannot be referenced elsewhere.
    """
    variables: list[str] = []
    for token in _VARIABLE_TOKEN.findall(_strip_quoted(text)):
        if token not in variables:
            variables.append(token)
    return variables


def body_of(rule: str) -> str:
    """Return the body of a rule/weak constraint as a comma-separated string
    without spaces (used to rebuild ``aux`` rules for weak constraints)."""
    text = rule.strip()
    if text.startswith(":~"):
        # Weak constraint: body is everything between ':~' and '[cost@level]'.
        text = text[2:].strip()
        bracket = _find_weight_bracket(text)
        if bracket >= 0:
            text = text[:bracket]
        return _normalize_spaces(strip_final_dot(text))

    if text.startswith("#minimize") or text.startswith("#maximize"):
        return _normalize_spaces(_body_from_optimization_statement(text))

    if ":-" in text:
        return _normalize_spaces(strip_final_dot(text.split(":-", 1)[1]))
    return _normalize_spaces(strip_final_dot(text))


def cost_of(rule: str) -> str:
    """Extract the ``cost@level[,terms]`` block of a weak constraint or
    #minimize/#maximize statement."""
    text = rule.strip()
    if text.startswith(":~"):
        start = _find_weight_bracket(text)
        if start >= 0:
            end = text.rfind("]")
            if end > start:
                return text[start + 1 : end].strip()

    match = re.search(r"(\S+@\S+?)(?:\s*:|\s*[;}])", text)
    if match:
        return match.group(1).strip()
    raise ValueError(f"Cannot read optimization cost from rule: {rule}")


def aggregate_expression(rule: str) -> str:
    """Return the aggregate atom of ``rule`` as written, including its
    guards: e.g. ``DUR = #sum{D,PH: duration(R,PH,D), PH != 4}`` or
    ``#count{X : p(X)} > 1``. Empty string when the rule has no aggregate."""
    match = re.search(r"(-?\w+\s*(?:!=|<=|>=|=|<|>)\s*)?#(?:count|sum)\{", rule)
    if not match:
        return ""
    brace = rule.find("{", match.start())
    end = _matching_brace(rule, brace)
    if end < 0:
        return ""
    expression = rule[match.start() : end + 1]
    # Guard on the right of the aggregate (e.g. "} > 1").
    tail = re.match(r"\s*(?:!=|<=|>=|=|<|>)\s*-?\w+", rule[end + 1 :])
    if tail:
        expression += tail.group(0)
    return expression.strip()


def search_terms(rule: str) -> dict[str, list[str]]:
    """Map each predicate appearing in the rule body (outside aggregates) to
    the list of its argument terms.

    Aggregate blocks (``#count{...}``/``#sum{...}``) are removed first, so
    only terms that can be bound by regular body literals are returned.
    """
    if ":-" in rule:
        text = "temp :- " + rule.split(":-", 1)[1]
    else:
        text = "temp :- " + rule
    body = _remove_aggregate_blocks(text.split(":-", 1)[1])

    terms: OrderedDict[str, list[str]] = OrderedDict()
    for predicate, args in _iter_predicate_calls(body):
        terms.setdefault(predicate, []).extend(
            term.strip() for term in split_top_level(args) if term.strip()
        )
    return dict(terms)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_quoted(text: str) -> str:
    """Replace the content of quoted strings with spaces so regexes cannot
    match inside them."""
    result: list[str] = []
    quote: str | None = None
    for i, char in enumerate(text):
        if quote:
            result.append(" ")
            if char == quote and text[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
            result.append(" ")
        else:
            result.append(char)
    return "".join(result)


def _find_weight_bracket(text: str) -> int:
    """Index of the '[' opening the weight block of a weak constraint,
    skipping brackets inside quoted strings."""
    quote: str | None = None
    for i, char in enumerate(text):
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "[":
            return i
    return -1


def _body_from_optimization_statement(text: str) -> str:
    """Collect the condition parts of a #minimize/#maximize statement."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ""
    bodies: list[str] = []
    for element in split_top_level(text[start + 1 : end], ";"):
        if ":" in element:
            bodies.append(element.split(":", 1)[1].strip())
    return ",".join(part for part in bodies if part)


def _iter_predicate_calls(text: str):
    """Yield ``(predicate, argument_text)`` for every predicate call in
    ``text``, skipping quoted strings and #-builtins."""
    i = 0
    quote: str | None = None
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue

        if char.isalpha() or char == "_":
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] == "_"):
                i += 1
            predicate = text[start:i]
            while i < len(text) and text[i].isspace():
                i += 1
            if i < len(text) and text[i] == "(" and not _is_builtin_context(text, start):
                end = _matching_parenthesis(text, i)
                if end > i:
                    yield predicate, text[i + 1 : end]
                    i = end + 1
                    continue
        i += 1


def _matching_parenthesis(text: str, start: int) -> int:
    """Index of the ')' matching the '(' at ``start`` (quote-aware)."""
    depth = 0
    quote: str | None = None
    for i in range(start, len(text)):
        char = text[i]
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _is_builtin_context(text: str, start: int) -> bool:
    """True when the identifier at ``start`` follows a '#' (e.g. #count)."""
    return "#" in text[max(0, start - 2) : start]


def _remove_aggregate_blocks(text: str) -> str:
    """Remove every ``#count{...}``/``#sum{...}`` block from ``text``."""
    result: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(text):
        char = text[i]
        if quote:
            result.append(char)
            if char == quote and text[i - 1] != "\\":
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(char)
            i += 1
            continue
        if text.startswith("#count{", i) or text.startswith("#sum{", i):
            brace = text.find("{", i)
            end = _matching_brace(text, brace)
            if end >= 0:
                i = end + 1
                continue
        result.append(char)
        i += 1
    return "".join(result)


def _matching_brace(text: str, start: int) -> int:
    """Index of the '}' matching the '{' at ``start`` (quote-aware)."""
    depth = 0
    quote: str | None = None
    for i in range(start, len(text)):
        char = text[i]
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1
