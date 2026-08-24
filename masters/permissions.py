from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

MASTERS_MANAGER_GROUPS = ["Purchase Officer (HO)", "SCM Head", "Admin"]


class MastersManagerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Front-end masters CRUD is for Purchase Officers / SCM Head / Admin.
    (Site members and Accounts are read-only or out of scope in Phase 1.)"""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or user.groups.filter(name__in=MASTERS_MANAGER_GROUPS).exists()
