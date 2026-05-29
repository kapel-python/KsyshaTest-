"""Tests for update_app_version_file — safe serialisation of release descriptions.

Covers: multiline text, double-quotes, apostrophes, emoji, Russian Unicode,
        and the pre-write syntax-validation guard.
"""

import ast
import importlib
import os
import shutil
import sys
import tempfile
import textwrap

# ── helpers ──────────────────────────────────────────────────────────────────

# We import the function under test directly from handlers.py by adding the
# workspace root to sys.path.  We do NOT start the bot; we only pull in the
# standalone helper.

_REPO = os.path.dirname(os.path.abspath(__file__))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load_writer():
    """Return update_app_version_file without importing the whole bot stack."""
    import importlib.util, types

    # Minimal stubs so handlers.py module-level imports don't crash.
    stubs = {
        "aiogram": types.ModuleType("aiogram"),
        "aiogram.types": types.ModuleType("aiogram.types"),
        "aiogram.filters": types.ModuleType("aiogram.filters"),
        "aiogram.fsm.context": types.ModuleType("aiogram.fsm.context"),
        "aiogram.fsm.state": types.ModuleType("aiogram.fsm.state"),
        "aiogram.enums": types.ModuleType("aiogram.enums"),
    }
    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

    # Import just the two helpers we need directly from source.
    # Avoids loading the full 9 k-line handlers module.
    src_path = os.path.join(_REPO, "handlers.py")
    with open(src_path, encoding="utf-8") as fh:
        source = fh.read()

    # Extract only the two standalone functions via AST + exec into a tiny ns.
    tree = ast.parse(source)
    kept = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "update_app_version_file",
            "increment_patch_version",
        ):
            kept.append(node)

    mini_mod = ast.Module(body=kept, type_ignores=[])
    ast.fix_missing_locations(mini_mod)
    ns: dict = {"os": os, "__name__": "__test__"}
    exec(compile(mini_mod, src_path, "exec"), ns)  # noqa: S102
    return ns["update_app_version_file"]


update_app_version_file = _load_writer()

# ── fixture: temporary copy of app_version.py ────────────────────────────────

_TEMPLATE = textwrap.dedent('''\
    """Single source of truth for application version metadata."""

    import subprocess
    import os

    version = "0.0.0"
    description = "placeholder"


    def get_version_metadata():
        pass
''')


def _make_temp_file() -> str:
    """Write the template into a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="app_version_test_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_TEMPLATE)
    return path


def _call(tmp_path: str, version: str, description: str):
    """Call update_app_version_file pointing at tmp_path via monkeypatching."""
    import app_version as av_mod

    original_get_repo_root = av_mod._get_repo_root
    # Make _get_repo_root return the dir containing our temp file.
    av_mod._get_repo_root = lambda: os.path.dirname(tmp_path)

    # Also point os.path.join to the temp file name.
    original_basename = os.path.basename(tmp_path)
    real_join = os.path.join

    import unittest.mock as mock

    with mock.patch("os.path.join", side_effect=lambda *a: (
        tmp_path if a[-1] == "app_version.py" else real_join(*a)
    )):
        try:
            update_app_version_file(version, description)
        finally:
            av_mod._get_repo_root = original_get_repo_root

    with open(tmp_path, encoding="utf-8") as f:
        return f.read()


def _parse_values(content: str) -> tuple[str, str]:
    """Parse version and description values from file content via ast.literal_eval."""
    tree = ast.parse(content)
    vals: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("version", "description"):
                    vals[t.id] = ast.literal_eval(node.value)
    return vals["version"], vals["description"]


# ── tests ────────────────────────────────────────────────────────────────────

def test_simple_ascii():
    tmp = _make_temp_file()
    try:
        content = _call(tmp, "1.2.3", "Simple release")
        ver, desc = _parse_values(content)
        assert ver == "1.2.3", f"version mismatch: {ver!r}"
        assert desc == "Simple release", f"description mismatch: {desc!r}"
        print("PASS  test_simple_ascii")
    finally:
        os.unlink(tmp)


def test_double_quotes_in_description():
    tmp = _make_temp_file()
    try:
        desc_input = 'Fix: "BUTTON_DATA_INVALID" error from Telegram'
        content = _call(tmp, "1.0.16", desc_input)
        compile(content, tmp, "exec")          # must be valid Python
        _, desc = _parse_values(content)
        assert desc == desc_input, f"round-trip failed: {desc!r}"
        print("PASS  test_double_quotes_in_description")
    finally:
        os.unlink(tmp)


def test_apostrophe_in_description():
    tmp = _make_temp_file()
    try:
        desc_input = "It's working now — user's fix"
        content = _call(tmp, "1.0.17", desc_input)
        compile(content, tmp, "exec")
        _, desc = _parse_values(content)
        assert desc == desc_input, f"round-trip failed: {desc!r}"
        print("PASS  test_apostrophe_in_description")
    finally:
        os.unlink(tmp)


def test_multiline_description():
    tmp = _make_temp_file()
    try:
        desc_input = "Line one\nLine two\nLine three"
        content = _call(tmp, "1.0.18", desc_input)
        compile(content, tmp, "exec")
        _, desc = _parse_values(content)
        assert desc == desc_input, f"round-trip failed: {desc!r}"
        print("PASS  test_multiline_description")
    finally:
        os.unlink(tmp)


def test_emoji_in_description():
    tmp = _make_temp_file()
    try:
        desc_input = "🚀 Deploy fix ✅ with emoji 🎉"
        content = _call(tmp, "1.0.19", desc_input)
        compile(content, tmp, "exec")
        _, desc = _parse_values(content)
        assert desc == desc_input, f"round-trip failed: {desc!r}"
        print("PASS  test_emoji_in_description")
    finally:
        os.unlink(tmp)


def test_russian_unicode():
    tmp = _make_temp_file()
    try:
        desc_input = "Исправление ошибки: кнопки не работали — «BUTTON_DATA_INVALID»"
        content = _call(tmp, "1.0.20", desc_input)
        compile(content, tmp, "exec")
        _, desc = _parse_values(content)
        assert desc == desc_input, f"round-trip failed: {desc!r}"
        print("PASS  test_russian_unicode")
    finally:
        os.unlink(tmp)


def test_combined_nightmare():
    """Multiline + quotes + apostrophes + emoji + Russian — all at once."""
    tmp = _make_temp_file()
    try:
        desc_input = (
            'Fix: "Telegram" сказал — "Bad Request"\n'
            "It's a multi-line\ndescription with 'apostrophes'\n"
            "и эмодзи 🚀🎉 и «кавычки»"
        )
        content = _call(tmp, "2.0.0", desc_input)
        compile(content, tmp, "exec")
        _, desc = _parse_values(content)
        assert desc == desc_input, f"round-trip failed: {desc!r}"
        print("PASS  test_combined_nightmare")
    finally:
        os.unlink(tmp)


def test_syntax_validation_catches_bad_input():
    """If the file would be invalid Python for any reason, SyntaxError is raised
    BEFORE the file is written, leaving the original untouched."""
    import unittest.mock as mock
    import app_version as av_mod

    tmp = _make_temp_file()
    original_content = open(tmp, encoding="utf-8").read()
    try:
        # Force compile() to raise by monkeypatching it inside the writer.
        real_compile = compile
        def bad_compile(src, *a, **kw):
            if "app_version" in str(a[0]):
                raise SyntaxError("injected failure")
            return real_compile(src, *a, **kw)

        av_mod_orig = av_mod._get_repo_root
        av_mod._get_repo_root = lambda: os.path.dirname(tmp)
        real_join = os.path.join
        try:
            with mock.patch("builtins.compile", side_effect=bad_compile), \
                 mock.patch("os.path.join", side_effect=lambda *a: (
                     tmp if a[-1] == "app_version.py" else real_join(*a)
                 )):
                try:
                    update_app_version_file("9.9.9", "should be blocked")
                    assert False, "Expected SyntaxError was not raised"
                except SyntaxError:
                    pass
        finally:
            av_mod._get_repo_root = av_mod_orig

        # File must be unchanged
        current = open(tmp, encoding="utf-8").read()
        assert current == original_content, "File was modified despite SyntaxError"
        print("PASS  test_syntax_validation_catches_bad_input")
    finally:
        os.unlink(tmp)


# ── runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_simple_ascii,
        test_double_quotes_in_description,
        test_apostrophe_in_description,
        test_multiline_description,
        test_emoji_in_description,
        test_russian_unicode,
        test_combined_nightmare,
        test_syntax_validation_catches_bad_input,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed.append(t.__name__)
    print()
    if failed:
        print(f"FAILED: {len(failed)}/{len(tests)}: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
