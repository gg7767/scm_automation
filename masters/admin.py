from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from masters.models import Item, ItemAlias, ItemCategory, Site, Vendor, VendorDocument


class ItemAliasInline(admin.TabularInline):
    model = ItemAlias
    extra = 1


class VendorDocumentInline(admin.TabularInline):
    model = VendorDocument
    extra = 1


@admin.register(Site)
class SiteAdmin(SimpleHistoryAdmin):
    list_display = ["code", "name", "is_factory", "active"]
    list_filter = ["is_factory", "active"]
    search_fields = ["code", "name"]


@admin.register(ItemCategory)
class ItemCategoryAdmin(SimpleHistoryAdmin):
    list_display = ["name", "parent"]
    list_filter = ["parent"]
    search_fields = ["name"]


@admin.register(Item)
class ItemAdmin(SimpleHistoryAdmin):
    list_display = ["code", "name", "category", "unit", "gst_rate", "active"]
    list_filter = ["category", "unit", "active"]
    search_fields = ["code", "name", "hsn_code", "aliases__alias_name"]
    inlines = [ItemAliasInline]


@admin.register(Vendor)
class VendorAdmin(SimpleHistoryAdmin):
    list_display = ["code", "name", "state", "status", "payment_terms_days"]
    list_filter = ["status", "state", "categories"]
    search_fields = ["code", "name", "gstin", "pan"]
    filter_horizontal = ["categories"]
    inlines = [VendorDocumentInline]
