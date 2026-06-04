from app.services.artifact_tools import task_artifact_dir
from app.services.engineering_repair import minimal_repair_files
from app.services.session_fs_tools import handle_write_file


def test_minimal_repair_adds_semicolon():
    task_id = "eng-min-repair"
    root = task_artifact_dir(task_id)
    root.mkdir(parents=True, exist_ok=True)
    handle_write_file(
        {
            "task_id": task_id,
            "path": "main.cpp",
            "content": "int main() { return 0 }",
            "parents": True,
        }
    )
    changed = minimal_repair_files(
        task_id,
        ["main.cpp"],
        stderr="error: expected ';' before '}'",
        intent_kind="code",
    )
    assert changed
    text = (root / "main.cpp").read_text(encoding="utf-8")
    assert text.rstrip().endswith(";")
