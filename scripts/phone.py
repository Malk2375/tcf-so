"""Démonstration sur téléphone : site de démonstration + Cloudflare Quick Tunnel (HTTPS).

Lancé par telephone.cmd, qui définit les variables de la démonstration et PHONE_TUNNEL=1.
cloudflared est pris dans le PATH, sinon dans .tools/ ; à défaut, il est téléchargé une fois
depuis les publications officielles de Cloudflare sur GitHub, avec vérification SHA-256.
Un Quick Tunnel ne demande aucun compte ; son adresse change à chaque lancement.
"""

import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

VERSION = "2026.10.7"
ASSET = "cloudflared-windows-amd64.exe"
SHA256 = "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343"
DOWNLOAD_URL = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{ASSET}"

BASE_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / ".tools"
LOG_FILE = BASE_DIR / "data" / "telephone-tunnel.log"
PORT = 8001
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
URL_TIMEOUT_SECONDS = 60


def tunnel_url(line: str) -> str | None:
    match = URL_PATTERN.search(line)
    return match.group(0) if match else None


def download(target: Path, url: str = DOWNLOAD_URL, sha256: str = SHA256) -> Path:
    print(f"Téléchargement de cloudflared {VERSION} depuis GitHub (Cloudflare)…")
    with urllib.request.urlopen(url) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit("Somme de contrôle SHA-256 incorrecte : téléchargement refusé.")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(data)
    return target


def cloudflared() -> Path:
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    local = TOOLS_DIR / f"cloudflared-{VERSION}.exe"
    return local if local.exists() else download(local)


def port_in_use(port: int = PORT) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def banner(url: str) -> str:
    width = max(len(url), 44) + 8
    line = "#" * width
    blank = "#" + " " * (width - 2) + "#"

    def row(text: str) -> str:
        return "#   " + text.ljust(width - 8) + "   #"

    return "\n".join(
        [
            "",
            line,
            blank,
            row("SUR LE TÉLÉPHONE, OUVREZ CETTE ADRESSE :"),
            blank,
            row(url),
            blank,
            row("Compte : admin@demo.test / demo-tcf-2026"),
            row("Adresse copiée dans le presse-papiers."),
            row("Pour arrêter : fermez cette fenêtre."),
            blank,
            line,
            "",
        ]
    )


def copy_to_clipboard(text: str) -> None:
    if os.name == "nt":
        subprocess.run(["clip"], input=text.encode("utf-16-le"), check=False)


def main() -> None:
    if os.environ.get("PHONE_TUNNEL") != "1":
        raise SystemExit("À lancer par telephone.cmd.")
    if port_in_use():
        raise SystemExit(
            f"Le port {PORT} est déjà utilisé : fermez la fenêtre de demo.cmd, puis relancez."
        )
    exe = cloudflared()
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", str(PORT), "--noreload"], cwd=BASE_DIR
    )
    LOG_FILE.parent.mkdir(exist_ok=True)
    tunnel = subprocess.Popen(
        [str(exe), "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    found = threading.Event()

    def read_log() -> None:
        with LOG_FILE.open("w", encoding="utf-8") as log:
            for line in tunnel.stderr:
                log.write(line)
                log.flush()
                url = tunnel_url(line)
                if url and not found.is_set():
                    found.set()
                    print(banner(url), flush=True)
                    copy_to_clipboard(url)

    threading.Thread(target=read_log, daemon=True).start()
    try:
        if not found.wait(URL_TIMEOUT_SECONDS):
            print(f"Aucune adresse reçue de Cloudflare : voir {LOG_FILE}.")
            return
        while server.poll() is None and tunnel.poll() is None:
            time.sleep(1)
        print("Le site ou le tunnel s'est arrêté.")
    except KeyboardInterrupt:
        pass
    finally:
        for process in (tunnel, server):
            if process.poll() is None:
                process.terminate()


if __name__ == "__main__":
    main()
