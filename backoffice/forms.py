from django import forms

from bank.constants import MAX_IMPORT_FILES
from bank.models import ExamFormat


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
