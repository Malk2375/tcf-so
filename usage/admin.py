from django.contrib import admin

from usage.models import SiteSettings, UsageEntry


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UsageEntry)
class UsageEntryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "provider", "model", "chars", "cost_usd", "month", "ref"]
    list_filter = ["kind", "month"]
