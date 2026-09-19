"""Explicit real Zhihu Open Platform smoke without printing credentials or content."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from otto_master.config import load_config
from otto_master.gateways.zhihu import ZhihuGateway


def _item_count(data: Any) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in ("Items", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


async def run(config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    if config.secrets.zhihu_access_secret is None:
        raise RuntimeError("ZHIHU_ACCESS_SECRET is required in the local environment")
    gateway = ZhihuGateway(config.zhihu, config.secrets.zhihu_access_secret)
    await gateway.start()
    try:
        quota = await gateway.quota()
        capabilities = quota.get("capabilities", [])
        available_ids = sorted(
            item["id"]
            for item in capabilities
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("available") is True
        )
        result = await gateway.call("hot", {"limit": 1})
        return {
            "verified": quota.get("verified") is True,
            "available_capabilities": available_ids,
            "query_tool": result.get("tool"),
            "query_source": result.get("source"),
            "query_item_count": _item_count(result.get("data")),
        }
    finally:
        await gateway.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.config)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
