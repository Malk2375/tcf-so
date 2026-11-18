from django.conf import settings


def site(request):
    return {"demo_mode": settings.DEMO}
