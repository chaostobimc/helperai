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
        operation = str(data.get("o", "")).upper()

        # Completion signal.
        if "status" in path and value == "FINISHED":
            return {"content": "", "type": "text", "finish_reason": "stop"}

        is_thinking = "thinking_content" in path or (
            "thinking_content" in self._last_patch_path and not path
        )
        is_content = path.endswith("/content") or (
            "/content" in self._last_patch_path and not path
        )

        # A snapshot can contain the beginning of the answer. Keep it instead
        # of discarding it; otherwise the first character/word may disappear.
        if isinstance(value, dict):
            response = value.get("response")
            snapshot = response.get("content") if isinstance(response, dict) else None
            if snapshot is None:
                snapshot = value.get("content")
            if isinstance(snapshot, str):
                state_name = "_patch_thinking" if is_thinking else "_patch_content"
                previous = getattr(self, state_name)
                setattr(self, state_name, snapshot)
                delta = snapshot[len(previous):] if snapshot.startswith(previous) else snapshot
                if delta:
                    return {
                        "content": delta,
                        "type": "thinking" if is_thinking else "text",
                        "finish_reason": None,
                    }
            self._last_patch_path = path or self._last_patch_path
            return None

        if not isinstance(value, str) or not (is_content or is_thinking):
            self._last_patch_path = path or self._last_patch_path
            return None

        state_name = "_patch_thinking" if is_thinking else "_patch_content"
        previous = getattr(self, state_name)
        if operation in {"SET", "REPLACE"}:
            updated = value
        else:
            # APPEND is the normal operation. Missing operation is treated as
            # APPEND for compatibility with observed DeepSeek events.
            updated = previous + value

        setattr(self, state_name, updated)
        self._last_patch_path = path or self._last_patch_path
        delta = updated[len(previous):] if updated.startswith(previous) else updated
        if not delta:
            return None
        return {
            "content": delta,
            "type": "thinking" if is_thinking else "text",
            "finish_reason": None,
        }
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
        init_anchor
        + "        self._last_patch_path = ''\n"
        + "        self._patch_content = ''\n"
        + "        self._patch_thinking = ''\n",
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

    stream_anchor = "            if chunk.startswith(b'data: '):\n                data = json.loads(chunk[6:])\n"
    if stream_anchor not in text:
        raise SystemExit("Unbekannte dsk/api.py-Version: SSE-Anker fehlt.")
    # SSE erlaubt sowohl "data: {...}" als auch "data:{...}". Der erste
    # Chunk kommt bei DeepSeek gelegentlich ohne Leerzeichen und würde sonst
    # genau das erste Wort der Antwort verschlucken.
    text = text.replace(
        stream_anchor,
        "            if chunk.startswith(b'data:'):\n"
        "                data = json.loads(chunk[5:].lstrip())\n",
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
