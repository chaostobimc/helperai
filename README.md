# Telegram-DeepSeek-Assistent für Raspberry Pi 5

Ein vollständiger Telegram-Bot als persönlicher KI-Assistent für eine
Telegram-App auf der Huawei Smartwatch.

- **Telegram:** `python-telegram-bot` ab Version 20, mit `async`/`await`
- **KI:** `xtekky/deepseek4free` und dessen Python-Modul `dsk`
- **Antworten:** Nutzung des Streaming-Generators `api.chat_completion(...)`
- **Thinking:** standardmäßig deaktiviert (`thinking_enabled=False`) für kurze,
schnelle Antworten
- **Zugriffsschutz:** ausschließlich Telegram-User-ID `8860332682`
- **Session:** DeepSeek-`chat_id` wird in `data/session.json` gespeichert
- **Autostart:** systemd mit automatischem Neustart nach Abstürzen

> **Wichtiger Hinweis zu `deepseek4free`:** Das Upstream-Repository spricht die
> nicht-offizielle DeepSeek-Web-App-Schnittstelle an und ist derzeit source-only
> (kein `setup.py`/`pyproject.toml`). Deshalb kann eine direkte
> `pip install git+https://github.com/xtekky/deepseek4free.git`-Installation mit
> aktuellem Upstream scheitern. Das mitgelieferte `install.sh` klont exakt dieses
> Repository nach `vendor/deepseek4free`, installiert dessen Abhängigkeiten und
> bindet `dsk` automatisch ein. So ist die Installation trotz der fehlenden
> Paketmetadaten reproduzierbar.

## 1. Ordnerstruktur

```text
telegram-deepseek-bot/
├── bot.py                    # vollständiger Bot
├── requirements.txt          # Telegram, dotenv und dokumentierte dsk-Quelle
├── install.sh                # venv + dsk-Quellcode + Abhängigkeiten
├── .env.example              # Vorlage, keine echten Geheimnisse
├── telegram-bot.service      # systemd-Vorlage
├── README.md
├── data/
│   └── .gitkeep              # session.json wird hier erzeugt
├── vendor/                   # lokal von install.sh geklont, nicht committen
└── .venv/                    # lokale virtuelle Umgebung, nicht committen
```

## 2. Docker-Installation (empfohlen)

Mit Docker brauchst du auf dem Raspberry Pi keine Python-Installation und
keine systemd-Datei für den Bot. Docker Compose übernimmt den Autostart mit
`restart: unless-stopped`. Raspberry Pi OS 64-bit wird empfohlen.

### Docker und Docker Compose installieren

```bash
sudo apt update
sudo apt install -y ca-certificates curl
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh
rm /tmp/get-docker.sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$(id -un)"
```

Danach **abmelden und wieder anmelden** (bei SSH die Verbindung schließen und
neu verbinden). Anschließend prüfen:

```bash
docker --version
docker compose version
```

Falls `docker compose` fehlt:

```bash
sudo apt update
sudo apt install -y docker-compose-plugin
```

### Projekt für Docker bereitstellen

Die folgenden Befehle laden den fertigen Projekt-Branch nach
`/opt/telegram-deepseek-bot`:

```bash
BOT_USER="$(id -un)"
BOT_GROUP="$(id -gn)"
sudo mkdir -p /opt/telegram-deepseek-bot
sudo chown "$BOT_USER:$BOT_GROUP" /opt/telegram-deepseek-bot
git clone \
  --branch arena/01a0ceb0-helperai \
  --single-branch \
  https://github.com/chaostobimc/helperai.git \
  /opt/telegram-deepseek-bot
cd /opt/telegram-deepseek-bot
```

### Konfiguration anlegen

```bash
umask 077
read -r -p "Telegram-Bot-Token: " TELEGRAM_TOKEN
read -r -s -p "DeepSeek-userToken: " DEEPSEEK_AUTH_TOKEN
printf '\n'
cat > .env <<EOF
TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
DEEPSEEK_AUTH_TOKEN=${DEEPSEEK_AUTH_TOKEN}
ALLOWED_USER_ID=8860332682
DEEPSEEK_TIMEOUT_SECONDS=180
SESSION_FILE=data/session.json
LOG_LEVEL=INFO
EOF
unset TELEGRAM_TOKEN DEEPSEEK_AUTH_TOKEN
chmod 600 .env
```

Den DeepSeek-Wert bekommst du in `chat.deepseek.com` über F12 → **Console**:

```javascript
JSON.parse(localStorage.getItem("userToken")).value
```

Nur den ausgegebenen Wert ohne Anführungszeichen in die Passwortabfrage oben
einfügen. Den Telegram-Wert erhältst du von `@BotFather`.

Wenn die Ausgabe `null` ist, bist du entweder nicht auf dem richtigen Ursprung
oder DeepSeek hat noch keine LocalStorage-Session angelegt. Prüfe zuerst:

```javascript
location.origin
Object.keys(localStorage)
```

`location.origin` muss `https://chat.deepseek.com` sein. Danach auf der Seite
anmelden, eine kurze Nachricht senden, die Seite neu laden und den folgenden
Code erneut ausführen:

```javascript
(() => {
  const raw = localStorage.getItem("userToken");
  if (!raw) {
    console.error("Kein userToken auf diesem Ursprung gefunden.");
    return;
  }

  try {
    const parsed = JSON.parse(raw);
    const token = parsed?.value ?? raw;
    console.log(token);
    copy(token);
  } catch (error) {
    console.log(raw);
    copy(raw);
  }
})();
```

Falls weiterhin kein `userToken` vorhanden ist, verwende den Network-Tab:

1. F12 → **Network** → Filter **Fetch/XHR**.
2. Auf `chat.deepseek.com` eine Nachricht senden.
3. Den Request an `chat.deepseek.com` öffnen.
4. Unter **Request Headers** nach `authorization: Bearer ...` suchen.
5. Den Wert nach `Bearer ` ohne das Präfix kopieren.

Diesen Token niemals hier posten oder in Git speichern.

### Image bauen und Container starten

```bash
mkdir -p data
touch data/deepseek-cookies.json
chmod 600 data/deepseek-cookies.json
sudo chown -R 1000:1000 data

docker compose build

# Einmalig DeepSeek-Cookies im Container erzeugen
docker compose exec telegram-bot python /app/docker/get_cookies.py

docker compose up -d
```

Status und Logs:

```bash
docker compose ps
docker compose logs -f telegram-bot
```

Die Log-Ausgabe mit `Ctrl+C` verlassen; der Container läuft weiter.

Befehle für den täglichen Betrieb:

```bash
# Neustart
docker compose restart

# Stoppen
docker compose stop

# Wieder starten
docker compose start

# Stoppen und Container entfernen (Session-Daten in ./data bleiben erhalten)
docker compose down

# Nach Code- oder Dockerfile-Änderungen neu bauen
docker compose up -d --build
```

Die Session bleibt in `data/session.json` erhalten. Der Compose-Service startet
nach einem Raspberry-Pi-Neustart automatisch. **Nicht zusätzlich** die
systemd-Datei für den Bot aktivieren, wenn Docker verwendet wird.

## 3. Alternative: Installation ohne Docker

Raspberry Pi OS 64-bit und eine aktuelle Python-Version werden empfohlen.
Mindestens Python 3.10 ist sinnvoll, weil `python-telegram-bot` v20 async ist.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

Projekt beispielsweise nach `/opt/telegram-deepseek-bot` kopieren oder dort
klonen. Die mitgelieferte systemd-Datei verwendet genau diesen Pfad und den
Benutzer `pi`:

```bash
sudo mkdir -p /opt/telegram-deepseek-bot
sudo cp -a . /opt/telegram-deepseek-bot/
sudo chown -R pi:pi /opt/telegram-deepseek-bot
cd /opt/telegram-deepseek-bot
```

Falls dein Linux-Benutzer nicht `pi` heißt, im Folgenden **sowohl**
`telegram-bot.service` (`User=`, `Group=` und Pfade) als auch die Befehle anpassen.

## 4. Virtuelle Umgebung und Dependencies installieren

### Empfohlener Weg

```bash
cd /opt/telegram-deepseek-bot
chmod +x install.sh
./install.sh
```

Das Skript verwendet automatisch `uv`, wenn es installiert ist, und fällt
sonst auf `pip` zurück. Es führt im Wesentlichen Folgendes aus:

1. `uv venv --python python3 .venv` (oder `python3 -m venv .venv`)
2. Installation von `python-telegram-bot` und `python-dotenv` mit `uv pip`
3. `git clone --depth 1 https://github.com/xtekky/deepseek4free.git vendor/deepseek4free`
4. Installation von `vendor/deepseek4free/requirements.txt` mit `uv pip`
5. Erzeugung der geschützten `.env`-Vorlage und des Datenordners

### Manuelle Installation mit uv

Wenn du jeden Schritt einzeln ausführen möchtest:

```bash
cd /opt/telegram-deepseek-bot
uv venv --python python3 .venv
uv pip install --python .venv/bin/python -r requirements.txt
mkdir -p vendor
git clone --depth 1 https://github.com/xtekky/deepseek4free.git vendor/deepseek4free
uv pip install --python .venv/bin/python -r vendor/deepseek4free/requirements.txt
mkdir -p data
chmod 700 data
```

Ohne uv lauten die beiden Installationszeilen alternativ:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r vendor/deepseek4free/requirements.txt
```

Die von dir gewünschte VCS-Referenz lautet:

```text
git+https://github.com/xtekky/deepseek4free.git
```

Sobald das Upstream-Projekt Paketmetadaten (`pyproject.toml` oder `setup.py`)
mitbringt, kann es direkt so installiert werden:

```bash
python -m pip install "git+https://github.com/xtekky/deepseek4free.git"
```

Beim derzeitigen source-only Stand ist der Clone samt dessen
`requirements.txt` der funktionierende Weg; `requirements.txt` dokumentiert die
VCS-URL deshalb ausdrücklich, statt einen bekannten pip-Fehler zu verstecken.

## 5. Telegram-Bot-Token und `.env` eintragen

1. Telegram öffnen und mit `@BotFather` einen Bot anlegen (`/newbot`).
2. Den dort ausgegebenen Bot-Token kopieren.
3. Konfiguration erzeugen und bearbeiten:

```bash
cd /opt/telegram-deepseek-bot
cp .env.example .env       # falls install.sh das noch nicht erledigt hat
nano .env
chmod 600 .env
```

Mindestens diese Werte müssen gesetzt werden:

```dotenv
TELEGRAM_TOKEN=123456789:DEIN_TOKEN_VON_BOTFATHER
DEEPSEEK_AUTH_TOKEN=DEIN_DEEPSEEK_USERTOKEN
ALLOWED_USER_ID=8860332682
DEEPSEEK_TIMEOUT_SECONDS=180
SESSION_FILE=data/session.json
```

`ALLOWED_USER_ID` wird als Integer verglichen. Nachrichten und Befehle von
anderen Telegram-IDs werden ohne Antwort ignoriert. Der Default im Code ist
zusätzlich `8860332682`, die Variable sollte trotzdem explizit in `.env` stehen.

## 6. DeepSeek-`userToken` aus der Web-App holen

`DEEPSEEK_AUTH_TOKEN` ist **nicht** der Telegram-Bot-Token. Es ist der Token aus
der LocalStorage von `https://chat.deepseek.com`.

1. `https://chat.deepseek.com` öffnen und anmelden.
2. Entwicklertools mit **F12** öffnen.
3. Zum Tab **Application** wechseln. Falls er nicht sichtbar ist, im `>>`-Menü
   auswählen.
4. Links **Local Storage** aufklappen und
   `https://chat.deepseek.com` auswählen.
5. Den Eintrag **`userToken`** suchen.
6. Im JSON-Wert das Feld **`value`** kopieren, nicht den Namen `userToken` und
   nicht die umgebenden Anführungszeichen.
7. Den Wert als `DEEPSEEK_AUTH_TOKEN` in `.env` eintragen.

Alternativ kann im Console-Tab der LocalStorage-Wert ausgelesen werden:

```javascript
JSON.parse(localStorage.getItem("userToken")).value
```

Der Token ist ein Geheimnis mit Zugriff auf dein DeepSeek-Webkonto. Nicht in
Git, Screenshots, Tickets oder Chatnachrichten speichern. Bei Verdacht auf
Offenlegung im DeepSeek-Konto abmelden bzw. den Token erneuern und danach die
`.env` aktualisieren.

## 7. Bot manuell testen

Vor der systemd-Einrichtung testen:

```bash
cd /opt/telegram-deepseek-bot
.venv/bin/python bot.py
```

Dann den Bot in Telegram mit `/start` anschreiben und eine kurze Frage senden.
Mit `/reset` wird eine neue DeepSeek-Session erzeugt, in
`data/session.json` gespeichert und der alte Kontext verworfen.

Mit `Ctrl+C` beenden. Die Logs zeigen keine Tokenwerte.

## 8. systemd-Autostart und Crash-Neustart

Die Datei `telegram-bot.service` ist bereits vollständig vorbereitet:

- Start nach verfügbarer Netzwerkverbindung
- Start mit dem eingeschränkten Benutzer `pi`
- `Restart=on-failure` und `RestartSec=10`
- Laden der Geheimnisse aus `/opt/telegram-deepseek-bot/.env`
- Session- und DeepSeek-Cookie-Verzeichnis bleibt schreibbar

Installieren und aktivieren:

```bash
sudo install -o root -g root -m 0644 \
  telegram-bot.service /etc/systemd/system/telegram-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot.service
```

Status und Live-Logs:

```bash
sudo systemctl status telegram-bot.service
sudo journalctl -u telegram-bot.service -f
```

Stoppen, neu starten oder Autostart deaktivieren:

```bash
sudo systemctl restart telegram-bot.service
sudo systemctl stop telegram-bot.service
sudo systemctl disable telegram-bot.service
```

Wenn das Projekt nicht unter `/opt/telegram-deepseek-bot` liegt, müssen
`WorkingDirectory`, `EnvironmentFile`, `ExecStart` und `ReadWritePaths` in der
Service-Datei entsprechend geändert werden. Ändert sich der Linux-Benutzer,
müssen `User=` und `Group=` ebenfalls angepasst werden.

## 9. Verhalten und Fehlerbehandlung

- Die User-ID wird vor `/start`, `/reset` und jeder Textnachricht geprüft.
- Während der synchronen Streaming-Generierung sendet der Bot regelmäßig den
  Telegram-Status `typing`. Der dsk-Stream wird in einem Worker-Thread gelesen,
  damit der asyncio-Bot nicht blockiert.
- `thinking_enabled=False` und `search_enabled=False` werden explizit an
  `api.chat_completion` übergeben. Nur Text-Chunks werden gesammelt und als
  Telegram-Antwort gesendet.
- Bekannte `dsk`-Fehler (`AuthenticationError`, `RateLimitError`,
  `NetworkError`, `CloudflareError`, `APIError`) sowie Timeouts werden in kurze
  deutsche Meldungen übersetzt.
- Telegram-Nachrichten über dem Telegram-Limit werden in mehrere Antworten
  geteilt.
- Die DeepSeek-`chat_id` bleibt über Bot-Neustarts erhalten. Eine kaputte oder
  abgelaufene Session kann jederzeit mit `/reset` ersetzt werden.

Falls Cloudflare eine Web-Schutzprüfung verlangt, verweist das Upstream-Projekt
auf seinen Bypass-Schritt. Aus dem Projektordner kann dieser – sofern auf dem
Raspberry Pi die dafür benötigte Browserumgebung vorhanden ist – so gestartet
werden:

```bash
cd /opt/telegram-deepseek-bot
PYTHONPATH=vendor/deepseek4free .venv/bin/python -m dsk.bypass
sudo systemctl restart telegram-bot.service
```

Die DeepSeek-Web-Schnittstelle und das Repository sind nicht die offizielle
bezahlte DeepSeek-API. Sie können sich ändern, Tokens können ablaufen und eine
Nutzung kann den Bedingungen des Dienstes unterliegen.
