from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from accounts_stub import roles


class TripAccessRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Log/view transport trips: Site Member, Purchase Officer, SCM Head, Admin."""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or roles.in_group(
            user, roles.SITE_MEMBER, roles.PURCHASE_OFFICER, roles.SCM_HEAD, roles.ADMIN
        )
