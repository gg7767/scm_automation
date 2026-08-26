from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from accounts_stub import roles


class AssetAccessRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """View machinery / log usage: Site Member, Purchase Officer, SCM Head, Admin."""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or roles.in_group(
            user, roles.SITE_MEMBER, roles.PURCHASE_OFFICER, roles.SCM_HEAD, roles.ADMIN
        )


class AssetManageRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Register machines / deployments: Purchase Officer, SCM Head, Admin."""

    def test_func(self):
        return roles.can_manage_purchase_orders(self.request.user) or roles.in_group(self.request.user, roles.ADMIN)
