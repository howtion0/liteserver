"""Verify the packaged runtime contains the complete offline Forge WebUI."""

from __future__ import annotations

import json
from importlib.resources import files


def main() -> None:
    root = files("otto_master").joinpath("web")
    required = (
        "index.html",
        "assembly.html",
        "art/forge-retro-monitor.png",
        "faces/neutral.gif",
        "models/body-clean.stl",
        "models/otto-reference.glb",
    )
    missing = [name for name in required if not root.joinpath(name).is_file()]
    assets = [item for item in root.joinpath("assets").iterdir() if item.is_file()]
    if missing or not any(item.name.endswith(".js") for item in assets):
        raise RuntimeError(
            f"packaged WebUI is incomplete: missing={missing}, assets={len(assets)}"
        )
    index = root.joinpath("index.html").read_text(encoding="utf-8")
    if "Forge 电台" not in index or "server-console" not in index:
        raise RuntimeError("packaged WebUI index is not the Forge Otto console")
    print(json.dumps({"status": "ok", "required": len(required), "assets": len(assets)}))


if __name__ == "__main__":
    main()
