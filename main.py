"""Start the local Obsidian knowledge workspace: uv run main.py."""

import argparse

import uvicorn
from dotenv import load_dotenv

from app.api import create_app
from app.config import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
