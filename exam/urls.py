from django.urls import path

from exam import api, views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("history/", views.history, name="history"),
    path("attempts/", views.attempt_create, name="attempt_create"),
    path("attempts/<int:pk>/", views.attempt_player, name="attempt_player"),
    path("attempts/<int:pk>/abandon/", views.attempt_abandon, name="attempt_abandon"),
    path("attempts/<int:pk>/results/", views.attempt_results, name="attempt_results"),
    path("attempts/<int:pk>/review/", views.attempt_review, name="attempt_review"),
    path("api/attempts/<int:attempt_id>/state", api.state, name="api_attempt_state"),
    path(
        "api/attempts/<int:attempt_id>/sections/<int:order>/start",
        api.start_section,
        name="api_section_start",
    ),
    path(
        "api/attempts/<int:attempt_id>/sections/<int:order>/finish",
        api.finish_section,
        name="api_section_finish",
    ),
    path("api/attempts/<int:attempt_id>/answers", api.answer, name="api_answers"),
    path(
        "api/attempts/<int:attempt_id>/co/audio-started",
        api.co_audio_started,
        name="api_co_audio_started",
    ),
    path("api/attempts/<int:attempt_id>/co/next", api.co_next, name="api_co_next"),
    path("api/attempts/<int:attempt_id>/abandon", api.abandon, name="api_abandon"),
]
