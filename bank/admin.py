from django.contrib import admin

from bank.models import (
    AudioAsset,
    Brief,
    ExamFormat,
    FormatSection,
    ImportBatch,
    Item,
    Test,
    TestItem,
)


class FormatSectionInline(admin.TabularInline):
    model = FormatSection
    extra = 0


@admin.register(ExamFormat)
class ExamFormatAdmin(admin.ModelAdmin):
    list_display = ["code", "mode", "label", "is_active"]
    inlines = [FormatSectionInline]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["id", "section", "level", "subtype", "status", "created_at"]
    list_filter = ["section", "level", "status"]
    search_fields = ["stimulus_text", "question", "content_hash"]
    readonly_fields = ["content_hash", "created_at"]


class TestItemInline(admin.TabularInline):
    model = TestItem
    extra = 0
    raw_id_fields = ["item"]


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ["__str__", "status", "published_at", "created_at"]
    list_filter = ["format", "status"]
    inlines = [TestItemInline]


@admin.register(Brief)
class BriefAdmin(admin.ModelAdmin):
    list_display = ["brief_id", "format", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ["id", "format", "status", "uploaded_by", "created_at"]


@admin.register(AudioAsset)
class AudioAssetAdmin(admin.ModelAdmin):
    list_display = ["item", "engine", "duration_ms", "chars_billed", "created_at"]
    raw_id_fields = ["item"]
