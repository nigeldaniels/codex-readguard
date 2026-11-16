"""Infer literal Python write targets; dynamic writers need explicit targets."""

import ast
from pathlib import Path

from .protocol import DECLARE_WRITES


def python_targets(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            targets.extend(call_targets(node))
    return targets


def call_targets(call: ast.Call) -> list[str]:
    function = call.func
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
    if name == "open":
        mode = next((kw.value for kw in call.keywords if kw.arg == "mode"),
                    call.args[1] if len(call.args) > 1 else ast.Constant("r"))
        if not isinstance(mode, ast.Constant) or not isinstance(mode.value, str):
            raise ValueError(DECLARE_WRITES)
        if not any(flag in mode.value for flag in "wax+"):
            return []
        path = call.args[0] if call.args else next(
            (kw.value for kw in call.keywords if kw.arg == "file"), None)
        return [required_path(path)]
    if not isinstance(function, ast.Attribute):
        return []
    if name in {"write_text", "write_bytes", "touch", "unlink"}:
        return [required_path(function.value)]
    if name in {"rename", "replace"}:
        if isinstance(function.value, ast.Name) and function.value.id == "os":
            return [required_path(arg) for arg in call.args[:2]]
        # str.replace and dataframe.rename are not file operations. Only infer
        # Path(...).rename/replace here; variable receivers use declared targets.
        if isinstance(function.value, ast.Call) and function_name(function.value.func) == "Path":
            return [required_path(function.value), required_path(call.args[0])]
    if name == "remove" and isinstance(function.value, ast.Name) and function.value.id == "os":
        return [required_path(call.args[0])]
    return []


def required_path(node) -> str:
    path = literal_path(node)
    if path is None:
        raise ValueError(DECLARE_WRITES)
    return path


def literal_path(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args:
        if function_name(node.func) == "Path":
            return literal_path(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = literal_path(node.left), literal_path(node.right)
        if left is not None and right is not None:
            return str(Path(left) / right)
    return None


def function_name(node) -> str:
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
