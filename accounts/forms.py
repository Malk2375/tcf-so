from django import forms
from django.conf import settings
from django.utils.crypto import constant_time_compare


class SignupCodeForm(forms.Form):
    """Champs ajoutés au formulaire d'inscription d'allauth (ACCOUNT_SIGNUP_FORM_CLASS).

    Le code d'invitation n'est demandé que si SIGNUP_CODE est renseigné.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.SIGNUP_CODE:
            self.fields["signup_code"] = forms.CharField(
                label="Code d'invitation",
                max_length=100,
                widget=forms.TextInput(attrs={"autocomplete": "off"}),
            )

    def clean_signup_code(self):
        code = self.cleaned_data["signup_code"].strip()
        if not constant_time_compare(code, settings.SIGNUP_CODE):
            raise forms.ValidationError("Code d'invitation incorrect.")
        return code

    def signup(self, request, user):
        pass


class DeleteAccountForm(forms.Form):
    password = forms.CharField(
        label="Mot de passe actuel",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    confirm = forms.BooleanField(
        label=(
            "Je comprends que mes passages, résultats et conseils seront supprimés définitivement."
        ),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Mot de passe incorrect.")
        return password
