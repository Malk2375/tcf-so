from django.urls import path

from backoffice import review_views, views

app_name = "backoffice"

urlpatterns = [
    path("", views.home, name="home"),
    path("briefs/", views.briefs, name="briefs"),
    path("briefs/<int:pk>/download/", views.brief_download, name="brief_download"),
    path("import/", views.import_view, name="import"),
    path("import/<int:pk>/", views.import_report, name="import_report"),
    path("tests/", review_views.tests, name="tests"),
    path("tests/<int:pk>/", review_views.test_review, name="test_review"),
    path("tests/<int:pk>/blind.json", review_views.blind_json, name="blind_json"),
    path("tests/<int:pk>/blind-answers", review_views.blind_answers, name="blind_answers"),
    path("tests/<int:pk>/audio/next", review_views.audio_next, name="audio_next"),
    path("tests/<int:pk>/publish", review_views.publish, name="publish"),
    path("tests/<int:pk>/archive", review_views.archive, name="archive"),
    path("tests/<int:pk>/delete", review_views.delete, name="delete"),
    path(
        "tests/<int:pk>/items/<int:item_pk>/status",
        review_views.item_status,
        name="item_status",
    ),
    path("tests/<int:pk>/items/<int:item_pk>/edit/", review_views.item_edit, name="item_edit"),
    path("tests/<int:pk>/items/<int:item_pk>/audio", review_views.item_audio, name="item_audio"),
    path("voices/", views.voices, name="voices"),
    path("voices/sample", views.voice_sample, name="voice_sample"),
    path("usage/", views.usage, name="usage"),
    path("users/", views.users, name="users"),
]
