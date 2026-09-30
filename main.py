"""Start the local Obsidian knowledge workspace: uv run main.py."""

import argparse
import webbrowser

import uvicorn
from dotenv import load_dotenv

from app.api import create_app
from app.config import ROOT


class LocalAppServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets)
        if self.started:
            webbrowser.open(f"http://127.0.0.1:{self.config.port}/", new=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    LocalAppServer(uvicorn.Config(create_app(), host="127.0.0.1", port=args.port)).run()


if __name__ == "__main__":
    main()
