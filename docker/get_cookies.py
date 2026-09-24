#!/usr/bin/env python3
"""Erzeugt den DeepSeek-Cookie-Cache im Docker-Container.

Die Upstream-Hilfe verlangt zwingend einen Cookie namens cf_clearance. Das ist
nicht mehr bei jeder DeepSeek-Auslieferung der verwendete Cookie-Name. Dieses
Skript akzeptiert deshalb jede erfolgreiche Cookie-Antwort des lokalen
Chromium-Bypass-Servers und schreibt sie im Format, das dsk erwartet.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


DSK_DIR = Path("/app/vendor/deepseek4free/dsk")
SERVER_SCRIPT = DSK_DIR / "server.py"
COOKIE_FILE = DSK_DIR / "cookies.json"
SERVER_URL = "http://127.0.0.1:8000/cookies?url=https://chat.deepseek.com"


def save_cookie_data(data: dict[str, Any]) -> None:
    cookies = data.get("cookies", {})
    if not isinstance(cookies, dict):
        raise RuntimeError("Der Cookie-Server lieferte kein gültiges cookies-Objekt.")

    output = {
        "cookies": {str(key): str(value) for key, value in cookies.items()},
        "user_agent": str(data.get("user_agent", "")),
    }
    COOKIE_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(COOKIE_FILE, 0o600)


def main() -> int:
    if not SERVER_SCRIPT.exists():
        print(f"Fehlt: {SERVER_SCRIPT}", file=sys.stderr)
        return 1

    environment = os.environ.copy()
    environment["DOCKERMODE"] = "true"

    process = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        cwd=str(DSK_DIR),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    try:
        response: requests.Response | None = None
        last_error: Exception | None = None

        for attempt in range(1, 7):
            try:
                response = requests.get(SERVER_URL, timeout=90)
                if response.status_code == 200:
                    break
                last_error = RuntimeError(
                    f"Cookie-Server HTTP {response.status_code}: {response.text[:200]}"
                )
            except requests.RequestException as error:
                last_error = error
            print(f"Versuch {attempt}/6: Cookie-Server noch nicht bereit ...")
            time.sleep(5)

        if response is None or response.status_code != 200:
            raise RuntimeError(f"Cookie-Server nicht erreichbar: {last_error}")

        data = response.json()
        save_cookie_data(data)
        cookie_names = ", ".join(sorted(data.get("cookies", {}).keys())) or "keine"
        print(f"Cookie-Datei aktualisiert: {COOKIE_FILE}")
        print(f"Gefundene Cookie-Namen: {cookie_names}")
        return 0
    except Exception as error:
        print(f"Cookie-Erzeugung fehlgeschlagen: {error}", file=sys.stderr)
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
