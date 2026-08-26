from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from accounts_stub import roles


class BillAccessRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Enter/view bills and payments: Accounts, SCM Head, Admin."""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or roles.in_group(user, roles.SCM_HEAD, roles.ACCOUNTS, roles.ADMIN)


class MismatchOverrideRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return roles.can_override_mismatch(self.request.user)
