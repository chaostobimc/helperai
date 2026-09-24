#!/usr/bin/env bash
# Installiert den Telegram-Bot reproduzierbar auf Raspberry Pi OS.
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${PROJECT_DIR}/.venv"
DEEPSEEK_DIR="${PROJECT_DIR}/vendor/deepseek4free"
DEEPSEEK_URL="https://github.com/xtekky/deepseek4free.git"

if ! command -v git >/dev/null 2>&1; then
    echo "Fehler: git ist nicht installiert. sudo apt install -y git" >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv"
    if [[ ! -d "${VENV_DIR}" ]]; then
        uv venv --python "${PYTHON_BIN}" "${VENV_DIR}"
    fi
else
    INSTALLER="pip"
    if [[ ! -d "${VENV_DIR}" ]]; then
        "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    fi
fi

PYTHON="${VENV_DIR}/bin/python"

if [[ "${INSTALLER}" == "uv" ]]; then
    install_packages() {
        uv pip install --python "${PYTHON}" "$@"
    }
else
    "${PYTHON}" -m pip install --upgrade pip setuptools wheel
    install_packages() {
        "${PYTHON}" -m pip install "$@"
    }
fi

install_packages -r "${PROJECT_DIR}/requirements.txt"

mkdir -p "${PROJECT_DIR}/vendor"
if [[ -d "${DEEPSEEK_DIR}/.git" ]]; then
    echo "deepseek4free ist bereits vorhanden: ${DEEPSEEK_DIR}"
elif [[ -e "${DEEPSEEK_DIR}" ]]; then
    echo "Fehler: ${DEEPSEEK_DIR} existiert, ist aber kein Git-Clone." >&2
    exit 1
else
    git clone --depth 1 "${DEEPSEEK_URL}" "${DEEPSEEK_DIR}"
fi

# Das Upstream-Repository ist source-only. Seine Laufzeitabhängigkeiten werden
# direkt aus der mitgelieferten requirements.txt installiert; bot.py fügt den
# Quellordner automatisch zum Importpfad hinzu.
install_packages -r "${DEEPSEEK_DIR}/requirements.txt"

mkdir -p "${PROJECT_DIR}/data"
chmod 700 "${PROJECT_DIR}/data"

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    echo
    echo "Eine .env-Datei wurde angelegt: ${PROJECT_DIR}/.env"
    echo "Bitte TELEGRAM_TOKEN und DEEPSEEK_AUTH_TOKEN eintragen."
else
    chmod 600 "${PROJECT_DIR}/.env"
fi

echo
echo "Installation abgeschlossen. Test: ${PYTHON} ${PROJECT_DIR}/bot.py"
