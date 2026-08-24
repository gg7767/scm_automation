from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from accounts_stub import roles


class POViewRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Read access: SCM Head, Purchase Officer, Accounts, Admin (all POs);
    Site Member (their own site's POs only, enforced by the view's queryset
    and get_object)."""

    def test_func(self):
        return roles.can_view_purchase_orders(self.request.user)


class POManageRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Create/edit/submit/send/cancel: SCM Head and Purchase Officer only."""

    def test_func(self):
        return roles.can_manage_purchase_orders(self.request.user)


class VendorLedgerViewRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Vendor ledgers aggregate a vendor's POs across all sites, so this is
    narrower than POViewRequiredMixin: Site Members are excluded (their PO
    visibility is meant to stay scoped to their own site's POs)."""

    def test_func(self):
        user = self.request.user
        return roles.can_view_purchase_orders(user) and not roles.is_site_restricted(user)


def visible_po_queryset(user, queryset):
    if roles.is_site_restricted(user):
        site = roles.user_site(user)
        return queryset.filter(site=site) if site else queryset.none()
    return queryset
