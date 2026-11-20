"""Import en lot des bruits de fond d'un dossier, d'après son manifeste `ambiences.json` (7.5.4)."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bank.ambiences import LICENSES, AmbienceError, add_clip


class Command(BaseCommand):
    help = (
        "Importe les bruits de fond d'un dossier d'après <dossier>/ambiences.json. Une entrée dont "
        "la licence n'est pas CC0, CC-BY ou CC-BY-NC (par exemple A_VERIFIER) est refusée. "
        "Relancer la commande ne crée pas de doublon."
    )

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Dossier des fichiers audio, par exemple assets/sound")

    def handle(self, *args, folder, **options):
        root = Path(folder)
        manifest = root / "ambiences.json"
        if not manifest.is_file():
            raise CommandError(f"Manifeste introuvable : {manifest}")
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Manifeste illisible : {exc}") from exc
        if not isinstance(entries, list):
            raise CommandError("Le manifeste doit être une liste d'entrées.")

        imported = refused = 0
        for number, entry in enumerate(entries, start=1):
            pattern = str(entry.get("file", ""))
            label = f"{number}. {pattern}"
            try:
                path = self._match(root, pattern)
                if entry.get("license") not in LICENSES:
                    raise AmbienceError(
                        f"licence « {entry.get('license')} » refusée (CC0, CC-BY ou CC-BY-NC)."
                    )
                result = add_clip(
                    str(path),
                    categories=entry.get("categories") or [],
                    title=entry.get("title") or "",
                    author=entry.get("author") or "",
                    source_url=entry.get("source_url") or "",
                    license=entry["license"],
                    start=entry.get("start"),
                    end=entry.get("end"),
                    notes=entry.get("notes") or "",
                    source_name=path.name,
                )
            except AmbienceError as exc:
                refused += 1
                self.stdout.write(self.style.ERROR(f"REFUSÉ  {label} : {exc}"))
                continue
            imported += 1
            clip = result.clip
            loudness = (
                f"{clip.loudness_lufs:.1f} LUFS".replace(".", ",")
                if clip.loudness_lufs is not None
                else "sonie non mesurable"
            )
            state = "ajouté" if result.created else "mis à jour"
            seconds = f"{clip.duration_ms / 1000:.1f}".replace(".", ",")
            categories = ", ".join(clip.categories)
            self.stdout.write(f"{state.upper():8}{label} → {categories} · {seconds} s · {loudness}")
            for warning in result.warnings:
                self.stdout.write(self.style.WARNING(f"        {warning}"))
        summary = f"{imported} importé(s), {refused} refusé(s)."
        self.stdout.write(
            self.style.SUCCESS(summary) if not refused else self.style.WARNING(summary)
        )

    def _match(self, root: Path, pattern: str) -> Path:
        if not pattern or "/" in pattern or "\\" in pattern or ".." in pattern:
            raise AmbienceError("nom de fichier invalide.")
        matches = sorted(
            p for p in root.glob(pattern) if p.is_file() and p.name != "ambiences.json"
        )
        if len(matches) != 1:
            found = ", ".join(p.name for p in matches) or "aucun"
            raise AmbienceError(
                f"le motif doit désigner exactement un fichier (trouvés : {found})."
            )
        return matches[0]
