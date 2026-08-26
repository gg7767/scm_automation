from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from indents.models import Indent, IndentApprovalAction, IndentLine


class IndentLineInline(admin.TabularInline):
    model = IndentLine
    extra = 0
    readonly_fields = ["qty_ordered"]


class IndentApprovalActionInline(admin.TabularInline):
    model = IndentApprovalAction
    extra = 0
    readonly_fields = ["action", "actor", "comment", "created_at"]
    can_delete = False


@admin.register(Indent)
class IndentAdmin(SimpleHistoryAdmin):
    list_display = ["indent_number", "site", "raised_by", "priority", "status", "required_by_date"]
    list_filter = ["status", "priority", "site"]
    search_fields = ["indent_number", "project_name"]
    readonly_fields = ["indent_number"]
    inlines = [IndentLineInline, IndentApprovalActionInline]
