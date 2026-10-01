import asyncio
import webbrowser

import uvicorn

from main import LocalAppServer


def test_browser_opens_only_after_server_starts(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, new: opened.append((url, new)))

    async def startup(self, sockets=None):
        self.started = True

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    server = LocalAppServer(uvicorn.Config("app.api:create_app", port=8766))
    asyncio.run(server.startup())
    assert opened == [("http://127.0.0.1:8766/", 2)]

    async def failed_startup(self, sockets=None):
        self.started = False

    monkeypatch.setattr(uvicorn.Server, "startup", failed_startup)
    asyncio.run(server.startup())
    assert len(opened) == 1
