import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from bank.devdata import fake_import_file
from bank.models import Brief, Test

pytestmark = pytest.mark.django_db


def upload(data, name="items.json"):
    return SimpleUploadedFile(name, json.dumps(data).encode(), content_type="application/json")


def test_pages_require_staff(client, django_user_model, formats):
    for name in ["backoffice:briefs", "backoffice:import"]:
        response = client.get(reverse(name))
        assert response.status_code == 302
    user = django_user_model.objects.create_user("u", "u@example.com", "Entrainement-TCF-2026")
    client.force_login(user)
    assert client.get(reverse("backoffice:briefs")).status_code == 403
    assert (
        client.post(reverse("backoffice:briefs"), {"exam_format": formats["rapide"].pk}).status_code
        == 403
    )
    assert client.post(reverse("backoffice:import")).status_code == 403
    assert Brief.objects.count() == 0


def test_brief_download(staff_client, formats):
    response = staff_client.post(reverse("backoffice:briefs"), {"exam_format": formats["moyen"].pk})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert (
        response["Content-Disposition"] == 'attachment; filename="brief_tcf_tp_so_moyen_0001.json"'
    )
    content = json.loads(response.content)
    assert content["brief_id"] == "tcf_tp_so_moyen-0001"
    assert len(content["slots"]) == 46

    brief = Brief.objects.get()
    page = staff_client.get(reverse("backoffice:briefs")).content.decode()
    assert "tcf_tp_so_moyen-0001" in page
    again = staff_client.get(reverse("backoffice:brief_download", args=[brief.pk]))
    assert json.loads(again.content) == content


def test_import_page_accepts_valid_file(staff_client, formats):
    response = staff_client.post(
        reverse("backoffice:briefs"), {"exam_format": formats["rapide"].pk}
    )
    data = fake_import_file(json.loads(response.content))
    response = staff_client.post(reverse("backoffice:import"), {"files": [upload(data)]})
    html = response.content.decode()
    assert "Import accepté" in html
    test = Test.objects.get()
    assert test.status == Test.Status.DRAFT
    assert test.import_batch.uploaded_by.email == "admin@example.com"
    assert test.import_batch.filenames == ["items.json"]


def test_import_page_shows_report_and_writes_nothing(staff_client, formats):
    response = staff_client.post(
        reverse("backoffice:briefs"), {"exam_format": formats["rapide"].pk}
    )
    data = fake_import_file(json.loads(response.content))
    data["items"][0]["correct_index"] = (data["items"][0]["correct_index"] + 1) % 4
    data["items"][-1]["stimulus_text"] = "<script>alert(1)</script>"
    response = staff_client.post(reverse("backoffice:import"), {"files": [upload(data, "co.json")]})
    html = response.content.decode()
    assert "Import refusé" in html
    assert ">Emplacement 1</span>" in html
    assert "<script>alert(1)</script>" not in html
    assert Test.objects.count() == 0


def test_import_page_accepts_three_files(staff_client, formats):
    response = staff_client.post(
        reverse("backoffice:briefs"), {"exam_format": formats["rapide"].pk}
    )
    data = fake_import_file(json.loads(response.content))
    files = [
        upload({**data, "items": [i for i in data["items"] if i["section"] == s]}, f"{s}.json")
        for s in ["CO", "MSL", "CE"]
    ]
    response = staff_client.post(reverse("backoffice:import"), {"files": files})
    assert "Import accepté" in response.content.decode()
    assert Test.objects.get().import_batch.filenames == ["CO.json", "MSL.json", "CE.json"]
