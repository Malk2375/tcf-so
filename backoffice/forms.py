import json

from django import forms

from bank.constants import MAX_IMPORT_FILES
from bank.models import ExamFormat
from bank.schema import StrictJSONError, parse_json


class BriefForm(forms.Form):
    exam_format = forms.ModelChoiceField(
        label="Mode",
        queryset=ExamFormat.objects.filter(is_active=True),
        empty_label=None,
        widget=forms.RadioSelect,
    )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".json,application/json"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, list | tuple):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)]


class ImportForm(forms.Form):
    files = MultipleFileField(
        label="Fichiers JSON",
        help_text=f"1 à {MAX_IMPORT_FILES} fichiers portant le même brief_id, 2 Mo au plus chacun.",
    )

    def clean_files(self):
        files = self.cleaned_data["files"]
        if len(files) > MAX_IMPORT_FILES:
            raise forms.ValidationError(f"{MAX_IMPORT_FILES} fichiers au plus.")
        return files


LETTER_CHOICES = [(index, letter) for index, letter in enumerate("ABCD")]


class ItemForm(forms.Form):
    """Modification d'un item à la relecture. Les contrôles de fond sont ceux de l'import."""

    stimulus_text = forms.CharField(label="Support", widget=forms.Textarea(attrs={"rows": 6}))
    audio_script = forms.CharField(
        label="Script audio (JSON)",
        widget=forms.Textarea(attrs={"rows": 12, "class": "font-mono text-sm"}),
        help_text="style, speakers, lines : même forme que dans le fichier d'import.",
    )
    question = forms.CharField(label="Question", required=False)
    option_0 = forms.CharField(label="Option A")
    option_1 = forms.CharField(label="Option B")
    option_2 = forms.CharField(label="Option C")
    option_3 = forms.CharField(label="Option D")
    correct_index = forms.TypedChoiceField(
        label="Bonne réponse", choices=LETTER_CHOICES, coerce=int, widget=forms.RadioSelect
    )
    explanation = forms.CharField(label="Explication", widget=forms.Textarea(attrs={"rows": 5}))

    def __init__(self, *args, item, **kwargs):
        self.item = item
        if "initial" not in kwargs and not args:
            kwargs["initial"] = {
                "stimulus_text": item.stimulus_text,
                "audio_script": json.dumps(item.audio_script, ensure_ascii=False, indent=2)
                if item.audio_script
                else "",
                "question": item.question,
                **{f"option_{i}": option for i, option in enumerate(item.options)},
                "correct_index": item.correct_index,
                "explanation": item.explanation,
            }
        super().__init__(*args, **kwargs)
        if item.section == "CO":
            del self.fields["stimulus_text"]
        else:
            del self.fields["audio_script"]

    def clean_audio_script(self):
        raw = self.cleaned_data["audio_script"]
        try:
            return parse_json(raw.encode())
        except StrictJSONError as exc:
            raise forms.ValidationError(f"JSON illisible : {exc}.") from exc

    def changes(self) -> dict:
        data = self.cleaned_data
        changes = {
            "question": data["question"],
            "options": [data[f"option_{i}"] for i in range(4)],
            "correct_index": data["correct_index"],
            "explanation": data["explanation"],
        }
        if self.item.section == "CO":
            changes["audio_script"] = data["audio_script"]
        else:
            changes["stimulus_text"] = data["stimulus_text"]
        return changes


class PasteOrFileForm(forms.Form):
    """Un fichier JSON ou un texte collé (l'un ou l'autre)."""

    file = forms.FileField(
        label="Fichier JSON",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".json,application/json"}),
    )
    text = forms.CharField(
        label="ou texte collé",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 5, "class": "font-mono text-sm"}),
    )
    missing_message = "Choisissez un fichier ou collez un texte."

    def clean(self):
        data = super().clean()
        has_file = bool(data.get("file"))
        has_text = bool((data.get("text") or "").strip())
        if has_file == has_text:
            raise forms.ValidationError(
                "Choisissez un fichier ou collez un texte, pas les deux."
                if has_file
                else self.missing_message
            )
        return data


class BlindAnswersForm(PasteOrFileForm):
    missing_message = (
        "Choisissez le fichier rendu par le relecteur, ou collez sa réponse "
        "(JSON, bloc ```json``` ou ligne « grille »)."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].label = "Réponses du relecteur : fichier JSON"
        self.fields["text"].label = "ou réponse collée (JSON, bloc ```json``` ou grille 1A · 2C …)"


class CorrectionsForm(PasteOrFileForm):
    missing_message = "Choisissez le fichier de corrections, ou collez son contenu."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].label = "Corrections : fichier JSON"
        self.fields["text"].label = "ou contenu collé (JSON ou bloc ```json```)"
