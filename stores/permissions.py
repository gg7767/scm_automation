from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from accounts_stub import roles


class GRNAccessRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Site Member (own site), Purchase Officer, SCM Head, Admin."""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or roles.in_group(
            user, roles.SITE_MEMBER, roles.PURCHASE_OFFICER, roles.SCM_HEAD, roles.ADMIN
        )


class GRNReversalRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Reversal GRNs (corrections) are HO-only."""

    def test_func(self):
        return roles.can_manage_purchase_orders(self.request.user)


def visible_grn_queryset(user, queryset):
    if roles.is_site_restricted(user):
        site = roles.user_site(user)
        return queryset.filter(site=site) if site else queryset.none()
    return queryset


def sites_for_user(user):
    from masters.models import Site
    if roles.is_site_restricted(user):
        site = roles.user_site(user)
        return Site.objects.filter(pk=site.pk) if site else Site.objects.none()
    return Site.objects.all()
