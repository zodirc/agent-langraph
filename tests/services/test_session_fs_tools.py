from pathlib import Path

from app.services.session_fs_tools import (
    handle_append_file,
    handle_copy_path,
    handle_grep_file,
    handle_ls_path,
    handle_mkdir_path,
    handle_move_path,
    handle_read_file,
    handle_rm_path,
    handle_replace_in_file,
    handle_touch_file,
    handle_write_file,
)


def test_touch_mkdir_grep_replace_inside_session(isolated_stores):
    task_id = "t-session-fs-1"
    handle_mkdir_path({"task_id": task_id, "path": "docs", "parents": True, "exist_ok": True})
    touch = handle_touch_file({"task_id": task_id, "path": "docs/a.txt", "parents": False})
    assert touch["status"] == "ok"

    with open(touch["path"], "w", encoding="utf-8") as f:
        f.write("alpha\nbeta\nalpha beta\n")

    grep_res = handle_grep_file(
        {"task_id": task_id, "path": "docs/a.txt", "pattern": "alpha", "ignore_case": False}
    )
    assert grep_res["matched_count"] == 2

    repl = handle_replace_in_file(
        {
            "task_id": task_id,
            "path": "docs/a.txt",
            "old_text": "alpha",
            "new_text": "gamma",
            "replace_all": True,
        }
    )
    assert repl["replacements"] == 2


def test_blocks_path_escape(isolated_stores):
    task_id = "t-session-fs-2"
    try:
        handle_touch_file({"task_id": task_id, "path": "../escape.txt", "parents": True})
    except ValueError as exc:
        assert "escapes session directory" in str(exc)
    else:
        raise AssertionError("Expected path escape ValueError")


def test_replace_regex_and_ignore_case(isolated_stores):
    task_id = "t-session-fs-3"
    handle_mkdir_path({"task_id": task_id, "path": "docs", "parents": True, "exist_ok": True})
    touched = handle_touch_file({"task_id": task_id, "path": "docs/re.txt", "parents": False})
    with open(touched["path"], "w", encoding="utf-8") as f:
        f.write("Foo 123\nfoo 456\nbar\n")

    replaced = handle_replace_in_file(
        {
            "task_id": task_id,
            "path": "docs/re.txt",
            "old_text": r"foo\s+\d+",
            "new_text": "baz",
            "regex": True,
            "ignore_case": True,
            "replace_all": True,
        }
    )
    assert replaced["replacements"] == 2


def test_replace_dry_run_returns_preview(isolated_stores):
    task_id = "t-session-fs-4"
    handle_mkdir_path({"task_id": task_id, "path": "docs", "parents": True, "exist_ok": True})
    touched = handle_touch_file({"task_id": task_id, "path": "docs/dr.txt", "parents": False})
    with open(touched["path"], "w", encoding="utf-8") as f:
        f.write("hello old\n")

    res = handle_replace_in_file(
        {
            "task_id": task_id,
            "path": "docs/dr.txt",
            "old_text": "old",
            "new_text": "new",
            "dry_run": True,
        }
    )
    assert res["dry_run"] is True
    assert "preview" in res and res["preview"]
    with open(touched["path"], "r", encoding="utf-8") as f:
        assert f.read() == "hello old\n"


def test_rm_path_file_and_dry_run_dir(isolated_stores):
    task_id = "t-session-fs-5"
    handle_mkdir_path({"task_id": task_id, "path": "docs/sub", "parents": True, "exist_ok": True})
    touched = handle_touch_file({"task_id": task_id, "path": "docs/sub/a.txt", "parents": False})

    dry = handle_rm_path({"task_id": task_id, "path": "docs/sub", "recursive": True, "dry_run": True})
    assert dry["dry_run"] is True
    assert dry["removed_type"] == "dir_recursive"

    dry_file = handle_rm_path({"task_id": task_id, "path": "docs/sub/a.txt", "dry_run": True})
    file_removed = handle_rm_path(
        {
            "task_id": task_id,
            "path": "docs/sub/a.txt",
            "preview_token": dry_file["preview_token"],
        }
    )
    assert file_removed["removed_type"] == "file"
    assert not Path(touched["path"]).exists()


def test_blocks_rm_session_root(isolated_stores):
    task_id = "t-session-fs-6"
    try:
        handle_rm_path({"task_id": task_id, "path": "."})
    except ValueError as exc:
        assert "session root" in str(exc)
    else:
        raise AssertionError("Expected root protection ValueError")


def test_ls_and_read_file(isolated_stores):
    task_id = "t-session-fs-7"
    handle_mkdir_path({"task_id": task_id, "path": "docs", "parents": True, "exist_ok": True})
    touched = handle_touch_file({"task_id": task_id, "path": "docs/b.txt", "parents": False})
    with open(touched["path"], "w", encoding="utf-8") as f:
        f.write("line-1\nline-2\nline-3\n")

    listing = handle_ls_path({"task_id": task_id, "path": "docs"})
    assert listing["status"] == "ok"
    assert any(item["path"] == "b.txt" for item in listing["entries"])

    read = handle_read_file(
        {"task_id": task_id, "path": "docs/b.txt", "offset": 0, "max_chars": 6}
    )
    assert read["content"] == "line-1"
    assert read["truncated"] is True


def test_write_append_move_copy_path(isolated_stores):
    task_id = "t-session-fs-8"
    write = handle_write_file(
        {
            "task_id": task_id,
            "path": "docs/w.txt",
            "content": "alpha",
            "parents": True,
        }
    )
    assert write["mode"] == "write"
    app = handle_append_file(
        {"task_id": task_id, "path": "docs/w.txt", "content": "\nbeta", "parents": False}
    )
    assert app["mode"] == "append"

    moved = handle_move_path(
        {"task_id": task_id, "src": "docs/w.txt", "dst": "docs/w2.txt", "parents": False}
    )
    assert moved["status"] == "ok"
    copied = handle_copy_path(
        {"task_id": task_id, "src": "docs/w2.txt", "dst": "docs/w3.txt", "parents": False}
    )
    assert copied["copied_type"] == "file"
    read = handle_read_file({"task_id": task_id, "path": "docs/w3.txt", "max_chars": 64})
    assert "alpha" in read["content"] and "beta" in read["content"]


def test_rm_requires_preview_token(isolated_stores):
    task_id = "t-session-fs-9"
    handle_write_file(
        {"task_id": task_id, "path": "docs/todel.txt", "content": "x", "parents": True}
    )
    try:
        handle_rm_path({"task_id": task_id, "path": "docs/todel.txt", "dry_run": False})
    except ValueError as exc:
        assert "preview_token required" in str(exc)
    else:
        raise AssertionError("Expected preview token required")

    dry = handle_rm_path({"task_id": task_id, "path": "docs/todel.txt", "dry_run": True})
    assert dry["dry_run"] is True
    token = dry.get("preview_token")
    assert token
    done = handle_rm_path(
        {
            "task_id": task_id,
            "path": "docs/todel.txt",
            "dry_run": False,
            "preview_token": token,
        }
    )
    assert done["removed_type"] == "file"
