from django.urls import path

from advice import views

urlpatterns = [
    path("advice/", views.advice_page, name="advice"),
    path("advice/<int:pk>/", views.advice_detail, name="advice_detail"),
    path("advice/<int:pk>/plan", views.plan_toggle, name="advice_plan"),
    path("attempts/<int:pk>/advice", views.attempt_advice, name="attempt_advice"),
]
