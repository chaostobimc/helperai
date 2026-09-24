#!/usr/bin/env python3
"""Add support for DeepSeek's current JSON-Patch SSE stream to dsk.

The original xtekky/deepseek4free client only parses the older
OpenAI-compatible ``choices[].delta`` stream. DeepSeek's web app now also sends
patch events such as ``{"p":"response/content","o":"APPEND","v":"..."}``.
This build-time patch keeps the requested upstream repository while making its
client understand both formats.
"""

from __future__ import annotations

import sys
from pathlib import Path


PATCH_METHOD = '''    def _parse_patch_chunk(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse DeepSeek's current JSON-Patch style stream event."""
        path = data.get("p", "")
        value = data.get("v")

        # Initial message snapshot: nothing to stream.
        if isinstance(value, dict):
            return None

        # Completion signal.
        if "status" in path and value == "FINISHED":
            return {"content": "", "type": "text", "finish_reason": "stop"}

        # Text deltas may omit the path after the first content event.
        if path.endswith("/content") or (
            "/content" in self._last_patch_path and not path
        ):
            self._last_patch_path = path or self._last_patch_path
            if isinstance(value, str) and value:
                return {"content": value, "type": "text", "finish_reason": None}
            return None

        # Thinking deltas are kept separate so bot.py can discard them.
        if "thinking_content" in path or (
            "thinking_content" in self._last_patch_path and not path
        ):
            self._last_patch_path = path or self._last_patch_path
            if isinstance(value, str) and value:
                return {
                    "content": value,
                    "type": "thinking",
                    "finish_reason": None,
                }
            return None

        self._last_patch_path = path
        return None
'''


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Aufruf: patch_dsk_api.py /pfad/zu/dsk/api.py")

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    if "def _parse_patch_chunk" in text:
        print(f"JSON-Patch-Unterstützung bereits vorhanden: {path}")
        return

    init_anchor = "        self.auth_token = auth_token\n"
    if init_anchor not in text:
        raise SystemExit("Unbekannte dsk/api.py-Version: Init-Anker fehlt.")
    text = text.replace(
        init_anchor,
        init_anchor + "        self._last_patch_path = ''\n",
        1,
    )

    parser_anchor = "                if 'choices' in data and data['choices']:\n"
    if parser_anchor not in text:
        raise SystemExit("Unbekannte dsk/api.py-Version: Parser-Anker fehlt.")
    text = text.replace(
        parser_anchor,
        "                if isinstance(data, dict) and 'v' in data:\n"
        "                    return self._parse_patch_chunk(data)\n\n"
        + parser_anchor,
        1,
    )

    method_anchor = "    def _parse_chunk(self, chunk: bytes) -> Optional[Dict[str, Any]]:\n"
    if method_anchor not in text:
        raise SystemExit("Unbekannte dsk/api.py-Version: Methoden-Anker fehlt.")
    text = text.replace(method_anchor, PATCH_METHOD + "\n" + method_anchor, 1)
    path.write_text(text, encoding="utf-8")
    print(f"JSON-Patch-Unterstützung hinzugefügt: {path}")


if __name__ == "__main__":
    main()
