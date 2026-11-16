from django.contrib import admin

from advice.models import Advice


@admin.register(Advice)
class AdviceAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "attempt", "source", "model", "cost_usd"]
    list_filter = ["source"]
    readonly_fields = ["created_at"]
    raw_id_fields = ["user", "attempt"]
