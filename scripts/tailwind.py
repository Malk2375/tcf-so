"""Compile la feuille de styles avec l'exécutable autonome de Tailwind CSS (sans Node.js).

    uv run python scripts/tailwind.py build   # compile et minifie static/css/app.css
    uv run python scripts/tailwind.py watch   # recompile à chaque modification

L'exécutable est téléchargé une fois dans .tools/ (ignoré par git), avec vérification SHA-256.
"""

import hashlib
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "v4.3.5"
RELEASE_URL = f"https://github.com/tailwindlabs/tailwindcss/releases/download/{VERSION}"

BASE_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / ".tools"
SOURCE = BASE_DIR / "assets" / "css" / "app.css"
OUTPUT = BASE_DIR / "static" / "css" / "app.css"


def asset_name() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if system == "Windows":
        return "tailwindcss-windows-x64.exe"
    if system == "Darwin":
        return f"tailwindcss-macos-{arch}"
    if system == "Linux":
        return f"tailwindcss-linux-{arch}"
    raise SystemExit(f"Système non pris en charge : {system} {machine}")


def binary() -> Path:
    name = asset_name()
    path = TOOLS_DIR / f"{VERSION}-{name}"
    if path.exists():
        return path

    print(f"Téléchargement de Tailwind CSS {VERSION} ({name})…")
    # Format du fichier : « <sha256>  ./<nom> » par ligne.
    with urllib.request.urlopen(f"{RELEASE_URL}/sha256sums.txt") as response:
        sums = {}
        for line in response.read().decode().splitlines():
            digest, _, filename = line.partition("  ")
            sums[filename.removeprefix("./")] = digest

    with urllib.request.urlopen(f"{RELEASE_URL}/{name}") as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sums.get(name):
        raise SystemExit("Somme de contrôle SHA-256 incorrecte : téléchargement refusé.")

    TOOLS_DIR.mkdir(exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o755)
    return path


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "build"
    args = [str(binary()), "-i", str(SOURCE), "-o", str(OUTPUT)]
    if command == "build":
        args.append("--minify")
    elif command == "watch":
        args.append("--watch")
    else:
        raise SystemExit("Usage : python scripts/tailwind.py [build|watch]")
    subprocess.run(args, cwd=BASE_DIR, check=True)


if __name__ == "__main__":
    main()
