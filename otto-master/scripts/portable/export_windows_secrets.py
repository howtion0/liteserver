"""Export Windows runtime secrets without placing them in the source archive."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

_ENV_KEYS = (
    "OTTO_ASR_API_KEY",
    "OTTO_TTS_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZHIHU_ACCESS_SECRET",
    "OTTO_CONSOLE_TOKEN",
)
_REQUIRED_CLOUD_KEYS = frozenset(
    {"OTTO_ASR_API_KEY", "OTTO_TTS_API_KEY", "DEEPSEEK_API_KEY"}
)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _credential_store(path: Path) -> tuple[bytes, dict[str, Any], int]:
    raw = path.read_bytes()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or parsed.get("version") != 1:
        raise ValueError("unsupported MQTT credential store")
    users = parsed.get("users")
    if not isinstance(users, list):
        raise TypeError("MQTT credential store has no users list")
    master = next(
        (
            item
            for item in users
            if isinstance(item, dict) and item.get("role") == "master"
        ),
        None,
    )
    if not isinstance(master, dict) or _text(master.get("password")) is None:
        raise ValueError("MQTT credential store has no master password")
    device_count = sum(
        isinstance(item, dict) and item.get("role") == "device" for item in users
    )
    return raw, master, device_count


def export_windows_secrets(project_root: Path, output: Path) -> dict[str, object]:
    """Create one mode-0600 text file containing env values and device credentials."""

    project_root = project_root.resolve()
    output = output.expanduser().resolve()
    values = dotenv_values(project_root / ".env")
    selected = {
        key: value
        for key in _ENV_KEYS
        if (value := _text(values.get(key))) is not None
    }
    missing = sorted(_REQUIRED_CLOUD_KEYS.difference(selected))
    if missing:
        raise ValueError(f"missing required cloud keys: {', '.join(missing)}")

    credential_path = project_root / ".local-secrets" / "mqtt-credentials.json"
    raw_credentials, master, device_count = _credential_store(credential_path)
    selected["OTTO_MQTT_MASTER_PASSWORD"] = str(master["password"])
    generated_provisioning_token = _text(values.get("OTTO_PROVISIONING_TOKEN")) is None
    selected["OTTO_PROVISIONING_TOKEN"] = _text(
        values.get("OTTO_PROVISIONING_TOKEN")
    ) or secrets.token_urlsafe(32)

    lines = [
        "# Otto Master Windows secrets. DO NOT COMMIT OR SHARE.",
        "# Copy with scripts/windows/restore-secrets.ps1; Base64 is not encryption.",
        "# The console token is optional while server.console_auth_required is false.",
        "",
    ]
    ordered_keys = (
        "OTTO_ASR_API_KEY",
        "OTTO_TTS_API_KEY",
        "DEEPSEEK_API_KEY",
        "ZHIHU_ACCESS_SECRET",
        "OTTO_MQTT_MASTER_PASSWORD",
        "OTTO_PROVISIONING_TOKEN",
        "OTTO_CONSOLE_TOKEN",
    )
    for key in ordered_keys:
        value = selected.get(key)
        if value is not None:
            lines.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    lines.extend(
        (
            "",
            "# Existing EVA broker identities; restore script writes this to",
            "# .local-secrets/mqtt-credentials.json.",
            "OTTO_MQTT_CREDENTIALS_JSON_BASE64="
            + base64.b64encode(raw_credentials).decode("ascii"),
            "",
        )
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines))
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    os.chmod(output, 0o600)
    return {
        "path": str(output),
        "environment_keys": [key for key in ordered_keys if key in selected],
        "device_credentials": device_count,
        "generated_provisioning_token": generated_provisioning_token,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export_windows_secrets(args.project_root, args.output)
    print(
        "exported Windows secrets:",
        result["path"],
        f"({len(result['environment_keys'])} env keys, "
        f"{result['device_credentials']} device credentials)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
