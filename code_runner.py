"""Small isolated worker used by SkillNova's Python practice runner.

It is intentionally a separate process.  This is a defence-in-depth demo
runner, not a security boundary equivalent to a container or VM.
"""
import ast
import contextlib
import io
import json
import sys


MAX_SOURCE_CHARS = 20_000
MAX_CAPTURED_CHARS = 4_096
BLOCKED_NAMES = {
    "__builtins__", "__import__", "breakpoint", "compile", "delattr",
    "dir", "eval", "exec", "getattr", "globals", "help", "input",
    "locals", "open", "setattr", "vars",
}
SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "Exception": Exception, "float": float,
    "int": int, "isinstance": isinstance, "len": len, "list": list,
    "max": max, "min": min, "print": print, "range": range,
    "reversed": reversed, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
}


class LimitedOutput(io.TextIOBase):
    def __init__(self):
        self.parts = []
        self.length = 0

    def write(self, value):
        value = str(value)
        if self.length + len(value) > MAX_CAPTURED_CHARS:
            raise RuntimeError("Output limit exceeded.")
        self.parts.append(value)
        self.length += len(value)
        return len(value)

    def getvalue(self):
        return "".join(self.parts)


def validate_source(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            raise ValueError("Imports and scope-changing statements are not allowed.")
        if isinstance(node, ast.Name) and (node.id in BLOCKED_NAMES or node.id.startswith("__")):
            raise ValueError(f"'{node.id}' is not allowed.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Private and special attributes are not allowed.")


def split_arguments(value):
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def parse_piece(piece):
    # Stored inputs are intentionally human-readable.  Parse lists, dicts and
    # numbers; preserve words, graph notation and parentheses as strings.
    if piece.startswith(("[", "{", "'", '\"')) or piece.lstrip("+-").replace(".", "", 1).isdigit():
        try:
            return ast.literal_eval(piece)
        except (ValueError, SyntaxError):
            pass
    return piece


def parse_arguments(test_input):
    return [parse_piece(part) for part in split_arguments(test_input.strip())]


def display_value(value):
    return value if isinstance(value, str) else repr(value)


def main():
    try:
        request = json.load(sys.stdin)
        source = request.get("code", "")
        if not isinstance(source, str) or len(source) > MAX_SOURCE_CHARS:
            raise ValueError("Source code is missing or exceeds the 20,000 character limit.")

        tree = ast.parse(source, mode="exec")
        validate_source(tree)
        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        if not functions:
            raise ValueError("Define a Python function; the runner calls the first function you define.")

        captured = LimitedOutput()
        namespace = {"__name__": "__submission__", "__builtins__": SAFE_BUILTINS}
        with contextlib.redirect_stdout(captured):
            exec(compile(tree, "<submission>", "exec"), namespace, namespace)
            result = namespace[functions[0]](*parse_arguments(request.get("input", "")))

        output = display_value(result) if result is not None else captured.getvalue().strip()
        if not output:
            raise ValueError("Your function returned no value. Return the answer for each test case.")
        if len(output) > MAX_CAPTURED_CHARS:
            raise ValueError("Output limit exceeded.")
        print(json.dumps({"ok": True, "output": output}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}))


if __name__ == "__main__":
    main()
