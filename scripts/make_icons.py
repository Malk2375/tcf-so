"""Génère les icônes PNG à partir de assets/icons/icon.svg (Chromium de Playwright).

uv run python scripts/make_icons.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "icons" / "icon.svg"
TARGET = ROOT / "static" / "icons"
SIZES = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "icon-maskable-512.png": 512,
    "apple-touch-icon.png": 180,
    "favicon-32.png": 32,
}


def main() -> None:
    svg = SOURCE.read_text(encoding="utf-8")
    TARGET.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, size in SIZES.items():
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(
                f'<html><body style="margin:0">'
                f'<div style="width:{size}px;height:{size}px">'
                f"{svg.replace('<svg ', f'<svg width="{size}" height="{size}" ', 1)}"
                f"</div></body></html>"
            )
            page.screenshot(
                path=TARGET / name, clip={"x": 0, "y": 0, "width": size, "height": size}
            )
            page.close()
            print(f"{name} : {size} × {size}")
        browser.close()


if __name__ == "__main__":
    main()
