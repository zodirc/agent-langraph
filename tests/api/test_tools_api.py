from fastapi.testclient import TestClient

from app.main import app


def test_list_tools(isolated_stores):
    client = TestClient(app)
    resp = client.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    names = {tool["name"] for tool in data["tools"]}
    assert "echo" in names
