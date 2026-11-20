"""Passages dans /admin/ (SPEC 8.1) : consultables et supprimables.

Supprimer un passage supprime ses épreuves, ses réponses et le conseil qu'il a déclenché :
c'est ainsi qu'un administrateur permet à quelqu'un de repasser un test.
"""

from django.contrib import admin

from exam.models import Attempt, AttemptSection


class AttemptSectionInline(admin.TabularInline):
    model = AttemptSection
    fields = ["order", "section", "status", "n_correct", "score", "level"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ["started_at", "user", "test", "status", "global_score", "global_level"]
    list_filter = ["status", "test__format__mode"]
    search_fields = ["user__email", "user__username"]
    list_select_related = ["user", "test__format"]
    date_hierarchy = "started_at"
    inlines = [AttemptSectionInline]
    readonly_fields = [
        "user",
        "test",
        "status",
        "started_at",
        "finished_at",
        "expires_at",
        "global_score",
        "global_level",
        "level_low",
        "level_high",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False
