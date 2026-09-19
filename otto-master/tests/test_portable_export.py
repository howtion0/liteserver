from __future__ import annotations

import base64
import json
import stat
import subprocess
import sys
from pathlib import Path

_EXPORT_SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "portable" / "export_windows_secrets.py"
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "otto-master"
    secret_dir = project / ".local-secrets"
    secret_dir.mkdir(parents=True)
    (project / ".env").write_text(
        "OTTO_ASR_API_KEY=asr-test\n"
        "OTTO_TTS_API_KEY=tts-test\n"
        "DEEPSEEK_API_KEY=llm-test\n"
        "ZHIHU_ACCESS_SECRET=zhihu-test\n"
        "OTTO_CONSOLE_TOKEN=console-test\n",
        encoding="utf-8",
    )
    credentials = {
        "version": 1,
        "users": [
            {
                "username": "otto-master",
                "password": "master-test",
                "client_id": "otto-master",
                "role": "master",
                "device_id": None,
            },
            {
                "username": "device-test",
                "password": "device-password",
                "client_id": "device-test",
                "role": "device",
                "device_id": "aabbccddeeff",
            },
        ],
    }
    (secret_dir / "mqtt-credentials.json").write_text(
        json.dumps(credentials),
        encoding="utf-8",
    )
    return project


def test_export_windows_secrets_keeps_code_archive_separate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    output = tmp_path / "windows-secrets.txt"

    completed = subprocess.run(
        [
            sys.executable,
            str(_EXPORT_SCRIPT),
            "--project-root",
            str(project),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    text = output.read_text(encoding="utf-8")
    encoded = next(
        line.split("=", 1)[1]
        for line in text.splitlines()
        if line.startswith("OTTO_MQTT_CREDENTIALS_JSON_BASE64=")
    )
    restored = json.loads(base64.b64decode(encoded))
    assert 'OTTO_ASR_API_KEY="asr-test"' in text
    assert 'OTTO_MQTT_MASTER_PASSWORD="master-test"' in text
    assert "OTTO_PROVISIONING_TOKEN=" in text
    assert restored["users"][1]["device_id"] == "aabbccddeeff"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    repeated = subprocess.run(
        [
            sys.executable,
            str(_EXPORT_SCRIPT),
            "--project-root",
            str(project),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode != 0


def test_export_windows_secrets_requires_voice_keys(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".env").write_text("DEEPSEEK_API_KEY=llm-test\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(_EXPORT_SCRIPT),
            "--project-root",
            str(project),
            "--output",
            str(tmp_path / "missing.txt"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "OTTO_ASR_API_KEY" in completed.stderr
