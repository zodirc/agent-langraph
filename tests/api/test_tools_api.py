from fastapi.testclient import TestClient

from app.main import app
from app.services.artifact_tools import task_artifact_dir


def test_list_tools(isolated_stores):
    client = TestClient(app)
    resp = client.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    names = {tool["name"] for tool in data["tools"]}
    assert "echo" in names


def test_task_files_list_and_content(isolated_stores):
    task_id = "t-files-api"
    base = task_artifact_dir(task_id)
    sub = base / "docs"
    sub.mkdir(parents=True, exist_ok=True)
    target = sub / "a.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    client = TestClient(app)

    list_resp = client.get(f"/tasks/{task_id}/files", params={"recursive": "true"})
    assert list_resp.status_code == 200
    entries = list_resp.json()["entries"]
    assert any(item.get("path") == "docs/a.txt" for item in entries)

    content_resp = client.get(
        f"/tasks/{task_id}/files/content",
        params={"path": "docs/a.txt", "offset": 0, "max_chars": 5},
    )
    assert content_resp.status_code == 200
    data = content_resp.json()
    assert data["content"] == "hello"
    assert data["truncated"] is True


def test_task_file_content_write(isolated_stores):
    task_id = "t-files-write-api"
    client = TestClient(app)
    put_resp = client.put(
        f"/tasks/{task_id}/files/content",
        json={"path": "docs/live.txt", "content": "hello live", "append": False},
    )
    assert put_resp.status_code == 200
    data = put_resp.json()
    assert data["status"] == "ok"
    assert data["total_chars"] == len("hello live")

    read_resp = client.get(
        f"/tasks/{task_id}/files/content",
        params={"path": "docs/live.txt", "offset": 0, "max_chars": 100},
    )
    assert read_resp.status_code == 200
    assert read_resp.json()["content"] == "hello live"
