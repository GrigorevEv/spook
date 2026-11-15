import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_DIRS = ("src", "tests", "scripts")
VENDORED = re.compile(r"src/spook/static/htmx[^/]*\.js")
DIRECTIVE = re.compile(r"#\s*(noqa(: [A-Z]+[0-9]+(, [A-Z]+[0-9]+)*)?|type: ignore(\[[\w, -]+\])?)")
COMMENT_MARKS = {
    ".js": re.compile(r"(^|\s)//|/\*"),
    ".css": re.compile(r"/\*"),
    ".html": re.compile(r"\{#|<!--"),
    ".sh": re.compile(r"(^|\s)#"),
}


def code_files():
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", *CODE_DIRS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for name in sorted(set(listed.split("\0"))):
        suffix = Path(name).suffix
        if (suffix == ".py" or suffix in COMMENT_MARKS) and not VENDORED.fullmatch(name) and (ROOT / name).is_file():
            yield name


def is_shebang(line_number, text):
    return line_number == 1 and text.startswith("#!")


def python_violations(source):
    lines = source.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        line_number = token.start[0]
        if token.type == tokenize.COMMENT and not (
            is_shebang(line_number, token.string) or DIRECTIVE.fullmatch(token.string.rstrip())
        ):
            yield line_number, token.string
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            yield node.lineno, lines[node.lineno - 1].strip()


def marked_violations(source, mark):
    for line_number, line in enumerate(source.splitlines(), start=1):
        if mark.search(line) and not is_shebang(line_number, line):
            yield line_number, line.strip()


def violations(name):
    source = (ROOT / name).read_text(encoding="utf-8")
    suffix = Path(name).suffix
    if suffix == ".py":
        return python_violations(source)
    return marked_violations(source, COMMENT_MARKS[suffix])


def test_code_has_no_comments_or_docstrings():
    found = [f"{name}:{line}: {text}" for name in code_files() for line, text in violations(name)]
    assert not found, f"{len(found)} комментариев и докстрингов:\n" + "\n".join(found)
