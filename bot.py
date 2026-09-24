#!/usr/bin/env python3
"""Telegram-Bot als persönlicher DeepSeek-Assistent.

Der DeepSeek-Client aus ``xtekky/deepseek4free`` arbeitet synchron und liefert
Antworten als Generator. Die blockierende Arbeit wird deshalb in einen Worker-
Thread ausgelagert, damit der asyncio-Event-Loop von python-telegram-bot frei
bleibt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import NetworkError as TelegramNetworkError
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


BASE_DIR = Path(__file__).resolve().parent

# Das Upstream-Repository ist derzeit source-only (ohne setup.py/pyproject.toml).
# install.sh legt es deshalb unter vendor/ ab. Dieser Pfad macht den Import auch
# bei einem manuellen Clone ohne PYTHONPATH-Konfiguration möglich.
VENDORED_DEEPSEEK_DIR = BASE_DIR / "vendor" / "deepseek4free"
if VENDORED_DEEPSEEK_DIR.is_dir():
    sys.path.insert(0, str(VENDORED_DEEPSEEK_DIR))

try:
    from dsk.api import (  # type: ignore[import-not-found]
        APIError,
        AuthenticationError,
        CloudflareError,
        DeepSeekAPI,
        NetworkError,
        RateLimitError,
    )
    DSK_IMPORT_ERROR: Optional[Exception] = None
except ImportError as import_error:  # pragma: no cover - nur bei falscher Installation
    # Die Fallback-Klassen erlauben eine verständliche Fehlermeldung in main(),
    # anstatt beim Import mit einem unklaren ModuleNotFoundError abzubrechen.
    DSK_IMPORT_ERROR = import_error
    DeepSeekAPI = None  # type: ignore[assignment,misc]

    class AuthenticationError(Exception):
        """Fallback, falls dsk nicht installiert ist."""

    class RateLimitError(Exception):
        """Fallback, falls dsk nicht installiert ist."""

    class NetworkError(Exception):
        """Fallback, falls dsk nicht installiert ist."""

    class CloudflareError(Exception):
        """Fallback, falls dsk nicht installiert ist."""

    class APIError(Exception):
        """Fallback, falls dsk nicht installiert ist."""


LOGGER = logging.getLogger("telegram-deepseek-bot")

# Die Antwort darf nicht länger als das Telegram-Limit werden. Ein kleiner
# Sicherheitsabstand lässt Raum für Telegram-interne UTF-8-Verarbeitung.
TELEGRAM_MAX_MESSAGE_LENGTH = 4_000
TYPING_REFRESH_SECONDS = 4
DEFAULT_ALLOWED_USER_ID = 8_860_332_682
DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class Settings:
    """Laufzeitkonfiguration aus Umgebungsvariablen bzw. .env."""

    telegram_token: str
    deepseek_auth_token: str
    allowed_user_id: int
    deepseek_timeout_seconds: float
    session_file: Path


def _required_environment_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Die Umgebungsvariable {name} fehlt. "
            "Bitte die .env-Datei aus .env.example anlegen und ausfüllen."
        )
    return value


def load_settings() -> Settings:
    """Liest und validiert die Bot-Konfiguration."""

    # override=False: Bereits gesetzte Variablen des systemd-Service bleiben
    # maßgeblich; die .env-Datei liefert nur fehlende Werte.
    load_dotenv(BASE_DIR / ".env", override=False)

    telegram_token = _required_environment_value("TELEGRAM_TOKEN")
    deepseek_auth_token = _required_environment_value("DEEPSEEK_AUTH_TOKEN")

    # ALLOWED_USER_ID ist die dokumentierte Bezeichnung. Der Alias
    # ALLOWED_TELEGRAM_USER_ID unterstützt auch die ursprüngliche Benennung.
    raw_allowed_user_id = os.getenv(
        "ALLOWED_USER_ID",
        os.getenv("ALLOWED_TELEGRAM_USER_ID", str(DEFAULT_ALLOWED_USER_ID)),
    ).strip()
    try:
        allowed_user_id = int(raw_allowed_user_id)
    except ValueError as exc:
        raise RuntimeError("ALLOWED_USER_ID muss eine ganze Zahl sein.") from exc
    if allowed_user_id <= 0:
        raise RuntimeError("ALLOWED_USER_ID muss größer als 0 sein.")

    raw_timeout = os.getenv(
        "DEEPSEEK_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)
    ).strip()
    try:
        deepseek_timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError(
            "DEEPSEEK_TIMEOUT_SECONDS muss eine Zahl in Sekunden sein."
        ) from exc
    if deepseek_timeout_seconds <= 0:
        raise RuntimeError("DEEPSEEK_TIMEOUT_SECONDS muss größer als 0 sein.")

    raw_session_file = os.getenv("SESSION_FILE", "data/session.json").strip()
    session_file = Path(raw_session_file).expanduser()
    if not session_file.is_absolute():
        session_file = BASE_DIR / session_file

    return Settings(
        telegram_token=telegram_token,
        deepseek_auth_token=deepseek_auth_token,
        allowed_user_id=allowed_user_id,
        deepseek_timeout_seconds=deepseek_timeout_seconds,
        session_file=session_file,
    )


class SessionStore:
    """Persistiert die DeepSeek-Chat-ID atomar in einer kleinen JSON-Datei."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Optional[str]:
        if not self.path.exists():
            return None

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Session-Datei konnte nicht gelesen werden: %s", exc)
            return None

        chat_id = data.get("chat_id") if isinstance(data, dict) else None
        if not isinstance(chat_id, str) or not chat_id.strip():
            LOGGER.warning("Session-Datei enthält keine gültige chat_id.")
            return None
        return chat_id

    def save(self, chat_id: str) -> None:
        """Schreibt die Datei per temp + os.replace, damit sie nie halb ist."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Optional[Path] = None

        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as session_file:
                json.dump(
                    {
                        "chat_id": chat_id,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    session_file,
                    indent=2,
                )
                session_file.write("\n")
                session_file.flush()
                os.fsync(session_file.fileno())

            # Token stehen nicht in dieser Datei, trotzdem sind Session-IDs
            # interne Daten und werden nur für den Besitzer lesbar gespeichert.
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)


def _collect_streaming_response(
    api: Any,
    chat_session_id: str,
    prompt: str,
) -> str:
    """Verarbeitet den synchronen Stream-Generator von dsk.

    ``dsk`` liefert Dictionaries wie ``{"type": "text", "content": "..."}``.
    Thinking-Chunks werden absichtlich nicht an Telegram weitergereicht. Die
    Anfrage setzt thinking_enabled explizit auf False, damit DeepSeek R1 nicht
    lange interne Denk-Ausgaben für die Smartwatch erzeugt.
    """
    text_parts: list[str] = []
    chunk_types: set[str] = set()

    chunks = api.chat_completion(
        chat_session_id,
        prompt,
        parent_message_id=None,
        thinking_enabled=False,
        search_enabled=False,
    )
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue

        chunk_type = str(chunk.get("type", "")).lower()
        chunk_types.add(chunk_type or "<missing>")

        # Die aktuelle dsk-Dokumentation nennt den Typ "text". Manche
        # DeepSeek-SSE-Antworten liefern jedoch den Inhalt ohne type-Feld oder
        # mit einem anderen Typ. Deshalb entscheidet der Inhalt, nicht nur der
        # Typ. Thinking-Chunks bleiben trotzdem grundsätzlich ausgeschlossen.
        if chunk_type == "thinking":
            continue

        content = chunk.get("content")
        if content:
            text_parts.append(str(content))

    response = "".join(text_parts).strip()
    if not response:
        LOGGER.warning(
            "DeepSeek-Stream enthielt keinen Antworttext (Chunk-Typen: %s).",
            ", ".join(sorted(chunk_types)) or "keine",
        )
    return response


class DeepSeekClient:
    """Asynchroner Adapter um den synchronen dsk-Client herum."""

    def __init__(
        self,
        api: Any,
        session_store: SessionStore,
        timeout_seconds: float,
    ) -> None:
        self.api = api
        self.session_store = session_store
        self.timeout_seconds = timeout_seconds
        self._chat_session_id = session_store.load()
        self._conversation_lock = asyncio.Lock()

    @property
    def has_saved_session(self) -> bool:
        return bool(self._chat_session_id)

    async def _create_session_locked(self) -> str:
        """Erstellt eine Session; der blockierende HTTP-Aufruf läuft im Thread."""
        chat_id = await asyncio.to_thread(self.api.create_chat_session)
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise RuntimeError("DeepSeek lieferte keine gültige chat_id.")

        await asyncio.to_thread(self.session_store.save, chat_id)
        self._chat_session_id = chat_id
        return chat_id

    async def _reset_serialized(self) -> str:
        async with self._conversation_lock:
            return await self._create_session_locked()

    async def reset(self) -> str:
        """Erstellt und persistiert eine komplett neue DeepSeek-Konversation."""
        worker = asyncio.create_task(self._reset_serialized())
        try:
            return await asyncio.wait_for(
                asyncio.shield(worker), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            worker.add_done_callback(self._consume_finished_task)
            LOGGER.warning(
                "Das Erstellen einer neuen DeepSeek-Session überschritt das Timeout von %.1f Sekunden.",
                self.timeout_seconds,
            )
            raise

    async def _complete_serialized(self, prompt: str) -> str:
        # Eine DeepSeek-Session soll nicht durch parallele Requests aus dem
        # Telegram-Update-Pool durcheinandergebracht werden.
        async with self._conversation_lock:
            if not self._chat_session_id:
                await self._create_session_locked()

            # Die Prüfung oben garantiert das; die lokale Variable hilft dem
            # Type-Checker und schützt zusätzlich vor einem inkonsistenten State.
            chat_session_id = self._chat_session_id
            if not chat_session_id:
                raise RuntimeError("Keine DeepSeek-Session verfügbar.")

            return await asyncio.to_thread(
                _collect_streaming_response,
                self.api,
                chat_session_id,
                prompt,
            )

    @staticmethod
    def _consume_finished_task(task: asyncio.Task[str]) -> None:
        """Verhindert unbemerkte Exceptions eines nach Timeout weiterlaufenden Threads."""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            LOGGER.exception("DeepSeek-Worker ist nach einem Timeout fehlgeschlagen.")

    async def complete(self, prompt: str) -> str:
        """Sendet einen Prompt und wartet höchstens timeout_seconds.

        Ein Thread lässt sich in Python nicht sicher abbrechen. Bei einem
        Timeout wird der Worker daher abgeschirmt und im Hintergrund beendet;
        die Sperre bleibt bis dahin aktiv, damit keine zwei Requests dieselbe
        DeepSeek-Konversation gleichzeitig verwenden.
        """
        worker = asyncio.create_task(self._complete_serialized(prompt))

        try:
            return await asyncio.wait_for(
                asyncio.shield(worker), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            worker.add_done_callback(self._consume_finished_task)
            LOGGER.warning(
                "DeepSeek-Request überschritt das Timeout von %.1f Sekunden.",
                self.timeout_seconds,
            )
            raise


def is_allowed_user(update: Update, allowed_user_id: int) -> bool:
    """Prüft ausschließlich die Telegram-User-ID, niemals nur den Chat-Namen."""
    user = update.effective_user
    return user is not None and user.id == allowed_user_id


def split_for_telegram(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Teilt lange Antworten bevorzugt an Leerzeichen/Zeilenumbrüchen."""
    remaining = text.strip()
    parts: list[str] = []

    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        cut_at = remaining.rfind("\n", 0, max_length + 1)
        if cut_at < max_length // 2:
            cut_at = remaining.rfind(" ", 0, max_length + 1)
        if cut_at <= 0:
            cut_at = max_length

        parts.append(remaining[:cut_at].rstrip())
        remaining = remaining[cut_at:].lstrip()

    return parts


async def keep_typing(bot: Any, telegram_chat_id: int) -> None:
    """Hält den Telegram-Status während der DeepSeek-Generierung aktiv."""
    try:
        while True:
            try:
                await bot.send_chat_action(
                    chat_id=telegram_chat_id,
                    action=ChatAction.TYPING,
                )
            except TelegramError as exc:
                # Ein Statusfehler soll die eigentliche KI-Antwort nicht stoppen.
                LOGGER.debug("Telegram typing action fehlgeschlagen: %s", exc)
            await asyncio.sleep(TYPING_REFRESH_SECONDS)
    except asyncio.CancelledError:
        # Normaler Pfad nach Ende der Antwort.
        return


def assistant_from_context(context: ContextTypes.DEFAULT_TYPE) -> DeepSeekClient:
    return context.application.bot_data["deepseek_client"]


def _error_message(exc: Exception) -> str:
    """Übersetzt bekannte Fehler in kurze, smartwatch-taugliche Meldungen."""
    if isinstance(exc, AuthenticationError):
        return "DeepSeek-Anmeldung fehlgeschlagen. Bitte DEEPSEEK_AUTH_TOKEN erneuern."
    if isinstance(exc, RateLimitError):
        return "DeepSeek meldet ein Rate-Limit. Bitte kurz warten."
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "DeepSeek antwortet gerade nicht rechtzeitig. Bitte später erneut versuchen."
    if isinstance(exc, CloudflareError):
        return "DeepSeek ist durch eine Web-Schutzprüfung blockiert. Bitte später erneut versuchen."
    if isinstance(exc, NetworkError):
        return "Netzwerkfehler bei DeepSeek. Bitte Verbindung prüfen und erneut versuchen."
    if isinstance(exc, APIError):
        return "DeepSeek meldet einen API-Fehler. Bitte später erneut versuchen."
    return "Es ist ein unerwarteter Fehler aufgetreten. Bitte später erneut versuchen."


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_user(update, context.application.bot_data["allowed_user_id"]):
        return

    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        "Hallo! Ich bin dein persönlicher DeepSeek-Assistent.\n\n"
        "Sende mir einfach eine Frage. Für eine neue, leere Konversation: /reset"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_user(update, context.application.bot_data["allowed_user_id"]):
        return

    message = update.effective_message
    if message is None:
        return

    try:
        await assistant_from_context(context).reset()
    except Exception as exc:
        LOGGER.exception("Neue DeepSeek-Session konnte nicht erstellt werden.")
        await message.reply_text(_error_message(exc))
        return

    await message.reply_text("Neue DeepSeek-Konversation gestartet. Der alte Kontext ist zurückgesetzt.")


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet normale Textnachrichten des einen erlaubten Benutzers."""
    if not is_allowed_user(update, context.application.bot_data["allowed_user_id"]):
        # Absichtlich keine Antwort an fremde IDs.
        return

    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return

    prompt = message.text.strip()
    if not prompt:
        return

    typing_task = asyncio.create_task(keep_typing(context.bot, chat.id))
    try:
        answer = await assistant_from_context(context).complete(prompt)
        if not answer:
            await message.reply_text("DeepSeek hat eine leere Antwort geliefert. Bitte erneut versuchen.")
            return

        for part in split_for_telegram(answer):
            await message.reply_text(part)
    except Exception as exc:
        LOGGER.exception("Fehler bei der DeepSeek-Anfrage.")
        await message.reply_text(_error_message(exc))
    finally:
        typing_task.cancel()
        await asyncio.gather(typing_task, return_exceptions=True)


async def telegram_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Letzte Fehler-Fallbacks für Updates, die außerhalb eines Handlers auftreten."""
    if isinstance(context.error, TelegramNetworkError):
        # python-telegram-bot wiederholt Polling-Requests bereits selbst. Ein
        # 502/temporärer Gateway-Fehler ist deshalb kein Bot-Crash und braucht
        # keinen kompletten Traceback im Journal.
        LOGGER.warning(
            "Telegram-API vorübergehend nicht erreichbar (%s); Polling wird automatisch wiederholt.",
            context.error,
        )
        return

    LOGGER.error("Unbehandelter Telegram-Fehler: %s", context.error, exc_info=context.error)


def create_application(settings: Settings, deepseek_api: Any) -> Application:
    session_store = SessionStore(settings.session_file)
    deepseek_client = DeepSeekClient(
        api=deepseek_api,
        session_store=session_store,
        timeout_seconds=settings.deepseek_timeout_seconds,
    )

    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data["allowed_user_id"] = settings.allowed_user_id
    application.bot_data["deepseek_client"] = deepseek_client

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message)
    )
    application.add_error_handler(telegram_error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx schreibt Telegram-Bot-Tokens sonst als Teil der Request-URL ins
    # Journal. Nur Warnungen und Fehler dieser Bibliotheken protokollieren.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        settings = load_settings()
    except RuntimeError as exc:
        LOGGER.error("Konfigurationsfehler: %s", exc)
        raise SystemExit(2) from exc

    if DeepSeekAPI is None:
        LOGGER.error(
            "Das Paket dsk konnte nicht importiert werden. "
            "Bitte install.sh ausführen bzw. vendor/deepseek4free installieren. "
            "Importfehler: %s",
            DSK_IMPORT_ERROR,
        )
        raise SystemExit(2)

    try:
        deepseek_api = DeepSeekAPI(settings.deepseek_auth_token)
    except Exception as exc:
        LOGGER.error("DeepSeek-Client konnte nicht initialisiert werden: %s", exc)
        raise SystemExit(2) from exc

    application = create_application(settings, deepseek_api)
    LOGGER.info(
        "Telegram-Bot gestartet. Antworten werden nur für ALLOWED_USER_ID=%s verarbeitet. Gespeicherte DeepSeek-Session: %s",
        settings.allowed_user_id,
        application.bot_data["deepseek_client"].has_saved_session,
    )

    # drop_pending_updates verhindert, dass beim Neustart alte Nachrichten
    # unerwartet erneut an DeepSeek geschickt werden.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
