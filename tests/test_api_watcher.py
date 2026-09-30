import time

from fastapi.testclient import TestClient

from app.api import create_app
from tests.conftest import write


def wait_until(check, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for filesystem change")


def test_api_boundary_dates_and_data_deletion(service):
    write(service, "노트.md", "## 업무\n결제 검토")
    service.indexer.reconcile()
    with TestClient(create_app(service.config, service)) as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/ask", json={"question": "결제"}).status_code == 403
        token = client.get("/api/session").json()["token"]
        headers = {"x-obsi-token": token}
        assert (
            client.post(
                "/api/ask",
                headers={**headers, "origin": "https://evil.example"},
                json={"question": "결제"},
            ).status_code
            == 403
        )
        assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400
        response = client.post("/api/ask", headers=headers, json={"question": "결제"})
        assert response.status_code == 200 and response.json()["evidence"]
        graph = client.get("/api/graph", params={"q": "결제"}).json()
        assert any(n["matched"] for n in graph["nodes"])
        assert response.json()["graph"]["scope"] == "answer"
        assert client.get("/api/graph", params={"q": "x" * 201}).status_code == 422
        assert (
            client.post(
                "/api/ask", headers=headers, json={"question": "결제", "start": "invalid"}
            ).status_code
            == 422
        )
        assert client.delete("/api/data", headers=headers).status_code == 200
        assert not client.get("/api/status").json()["vault"]
        assert service.indexer.root is None


def test_native_watcher_create_modify_rename_delete(service):
    service.start()
    file = write(service, "자동.md", "## 업무\n첫 저장")
    wait_until(
        lambda: service.store.rows("SELECT * FROM notes WHERE path='자동.md' AND state='ready'")
    )
    original = service.store.rows("SELECT * FROM notes WHERE path='자동.md'")[0]
    file.write_text("## 업무\n연속 저장 1")
    file.write_text("## 업무\n연속 저장 2")
    file.write_text("## 업무\n마지막 저장")
    wait_until(
        lambda: (
            service.store.rows("SELECT * FROM sections WHERE text LIKE '%마지막%'")
            and service.store.rows("SELECT * FROM notes WHERE state='ready'")
        )
    )
    new_file = file.with_name("이동.md")
    file.rename(new_file)
    wait_until(
        lambda: service.store.rows("SELECT * FROM notes WHERE path='이동.md' AND state='ready'")
    )
    assert service.store.rows("SELECT id FROM notes")[0]["id"] == original["id"]
    new_file.unlink()
    wait_until(lambda: not service.store.rows("SELECT * FROM notes"))


def test_startup_reconciles_offline_change(service):
    file = write(service, "노트.md", "첫 기록")
    service.indexer.reconcile()
    file.write_text("종료 중 변경한 기록")
    service.start()
    wait_until(
        lambda: (
            service.store.rows("SELECT * FROM sections WHERE text LIKE '%종료 중%'")
            and service.store.rows("SELECT * FROM notes WHERE state='ready'")
        )
    )
