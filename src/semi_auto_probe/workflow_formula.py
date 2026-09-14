"""Bounded expression interpreter; never executes Python source."""
from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class InvalidValue:
    reason: str

    def __bool__(self):
        return False


@dataclass(frozen=True)
class Curve:
    x: tuple[float, ...]
    y: tuple[float, ...]
    label: str = ""
    x_unit: str = "s"
    y_unit: str = "A"


def _values(value):
    values = np.asarray(value.y if isinstance(value, Curve) else value, dtype=float)
    if not values.size or not np.all(np.isfinite(values)):
        raise ValueError("Empty or non-finite curve")
    return values


def _integral(curve):
    if not isinstance(curve, Curve) or len(curve.x) < 2 or len(curve.x) != len(curve.y):
        raise ValueError("Integral requires a curve with at least two paired samples")
    x, y = _values(curve.x), _values(curve)
    return float(np.sum(np.diff(x) * (y[:-1] + y[1:]) / 2))


def _slope(curve):
    if not isinstance(curve, Curve) or len(curve.x) < 2 or len(curve.x) != len(curve.y):
        raise ValueError("Slope requires a curve with at least two paired samples")
    x, y = _values(curve.x), _values(curve)
    dx = x - x.mean()
    denominator = float(dx @ dx)
    if denominator == 0:
        raise ValueError("Slope requires distinct X coordinates")
    return float(dx @ (y - y.mean()) / denominator)


def _segment(curve, start, stop):
    if not isinstance(curve, Curve):
        raise ValueError("segment requires a curve")
    start, stop = int(start), int(stop)
    return Curve(curve.x[start:stop], curve.y[start:stop], curve.label, curve.x_unit, curve.y_unit)


FUNCTIONS = {
    "abs": abs, "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos,
    "min": lambda *args: float(np.min(_values(args[0] if len(args) == 1 else args))),
    "max": lambda *args: float(np.max(_values(args[0] if len(args) == 1 else args))),
    "mean": lambda v: float(np.mean(_values(v))), "std": lambda v: float(np.std(_values(v))),
    "peak": lambda v: float(np.max(_values(v))), "integral": _integral, "slope": _slope,
    "segment": _segment,
}
BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
COMPARE = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}


def parse_expression(expression, variables):
    if len(expression) > 2048:
        raise ValueError("Formula is limited to 2048 characters")
    tree = ast.parse(expression, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 256:
        raise ValueError("Formula is too complex")
    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Call, ast.BinOp,
               ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.And, ast.Or,
               ast.Not, ast.USub, ast.UAdd, *BINOPS, *COMPARE)
    for node in nodes:
        if not isinstance(node, allowed):
            raise ValueError(f"Unsupported formula syntax: {type(node).__name__}")
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float, bool):
            raise ValueError("Only numeric and Boolean constants are supported")
        if isinstance(node, ast.Name) and node.id not in set(variables) | set(FUNCTIONS) | {"pi", "e"}:
            raise ValueError(f"Unknown variable: {node.id}")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords):
            raise ValueError("Only documented positional function calls are allowed")
    return tree


def evaluate_expression(expression, variables, output_type="number", policy="invalid"):
    try:
        tree = parse_expression(expression, variables)

        def visit(node):
            if isinstance(node, ast.Constant):
                return float(node.value) if type(node.value) is int else node.value
            if isinstance(node, ast.Name):
                value = variables.get(node.id, {"pi": math.pi, "e": math.e}.get(node.id))
                if value is None or isinstance(value, InvalidValue):
                    raise ValueError(value.reason if isinstance(value, InvalidValue) else f"Missing input: {node.id}")
                if isinstance(value, (int, float)) and not math.isfinite(value):
                    raise ValueError(f"Non-finite input: {node.id}")
                return value
            if isinstance(node, ast.Call):
                return FUNCTIONS[node.func.id](*(visit(arg) for arg in node.args))
            if isinstance(node, ast.BinOp):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Pow) and abs(b) > 1024:
                    raise ValueError("Exponent exceeds supported range")
                return BINOPS[type(node.op)](a, b)
            if isinstance(node, ast.UnaryOp):
                value = visit(node.operand)
                return not value if isinstance(node.op, ast.Not) else -value if isinstance(node.op, ast.USub) else +value
            if isinstance(node, ast.Compare):
                a = visit(node.left)
                for op, right in zip(node.ops, node.comparators):
                    b = visit(right)
                    if not COMPARE[type(op)](a, b):
                        return False
                    a = b
                return True
            if isinstance(node, ast.BoolOp):
                return all(bool(visit(v)) for v in node.values) if isinstance(node.op, ast.And) else any(bool(visit(v)) for v in node.values)
            if isinstance(node, ast.IfExp):
                return visit(node.body if visit(node.test) else node.orelse)
            raise ValueError("Unsupported expression")

        result = visit(tree.body)
        if output_type == "bool":
            if not isinstance(result, (bool, np.bool_)):
                raise ValueError("Formula must return a Boolean")
            return bool(result)
        if isinstance(result, (bool, np.bool_, Curve)) or not isinstance(result, (float, int, np.number)) or not math.isfinite(result):
            raise ValueError("Formula must return a finite number")
        return float(result)
    except (ValueError, TypeError, SyntaxError, ArithmeticError, RecursionError) as exc:
        if policy == "stop":
            raise ValueError(f"Formula failed: {exc}") from exc
        return InvalidValue(str(exc))
