import subprocess

import pytest
from fastapi.testclient import TestClient

from app import folder_picker
from app.api import create_app


def test_folder_choice_handles_unicode_spaces_and_quotes_without_script_interpolation(
    tmp_path, monkeypatch
):
    vault = tmp_path / '내 투자 볼트 "quoted" $(literal)'
    vault.mkdir()
    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")

    def run(command, **options):
        assert command == ["/usr/bin/osascript", "-e", folder_picker._CHOOSE_FOLDER, str(vault)]
        assert str(vault) not in command[2] and not options.get("shell")
        return subprocess.CompletedProcess(command, 0, str(vault) + "/\n", "")

    monkeypatch.setattr(folder_picker.subprocess, "run", run)
    assert folder_picker.choose_vault_folder(str(vault)) == str(vault)


def test_cancel_keeps_path_empty_and_invalid_start_uses_home(monkeypatch):
    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")

    def run(command, **options):
        assert command[-1] == str(folder_picker.Path.home())
        return subprocess.CompletedProcess(command, 0, "\n", "")

    monkeypatch.setattr(folder_picker.subprocess, "run", run)
    assert folder_picker.choose_vault_folder("relative/not-a-folder") is None


@pytest.mark.parametrize("timeout", [False, True])
def test_failed_picker_releases_dialog_lock(monkeypatch, timeout):
    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")

    def run(command, **options):
        if timeout:
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, 1, "", "picker unavailable")

    monkeypatch.setattr(folder_picker.subprocess, "run", run)
    with pytest.raises(ValueError, match="[Ff]older selection"):
        folder_picker.choose_vault_folder()
    assert not folder_picker._dialog_lock.locked()


def test_duplicate_dialog_is_rejected(monkeypatch):
    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")
    with (
        folder_picker._dialog_lock,
        pytest.raises(ValueError, match="folder selection window is already open"),
    ):
        folder_picker.choose_vault_folder()


def test_picker_api_requires_token_and_only_returns_a_path(service, tmp_path, monkeypatch):
    selected = tmp_path / "다른 볼트"
    selected.mkdir()
    original = service.store.get("vault")
    calls = []

    def choose(initial):
        calls.append(initial)
        return str(selected) if len(calls) == 1 else None

    monkeypatch.setattr("app.api.choose_vault_folder", choose)
    monkeypatch.setattr(service, "start", lambda: None)
    with TestClient(create_app(service.config, service)) as client:
        endpoint = "/api/vault/pick-folder"
        assert client.post(endpoint, json={}).status_code == 403
        headers = {"x-obsi-token": client.get("/api/session").json()["token"]}
        assert (
            client.post(
                endpoint, headers={**headers, "origin": "https://other.example"}, json={}
            ).status_code
            == 403
        )
        assert not calls
        assert client.post(endpoint, headers=headers, json={}).json() == {"path": str(selected)}
        assert client.post(
            endpoint, headers=headers, json={"initial_path": str(selected)}
        ).json() == {"path": None}
        assert calls == [original, str(selected)]
        assert service.store.get("vault") == original
        assert not service.store.rows("SELECT * FROM notes")
