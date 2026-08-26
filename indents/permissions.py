from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from accounts_stub import roles


class IndentAccessRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Anyone who can view/raise indents: Site Member (own site, enforced by
    queryset/get_object), Purchase Officer, SCM Head, Admin."""

    def test_func(self):
        user = self.request.user
        return user.is_superuser or roles.in_group(
            user, roles.SITE_MEMBER, roles.PURCHASE_OFFICER, roles.SCM_HEAD, roles.ADMIN
        )


class IndentConversionRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Indent -> PO conversion is an HO action: Purchase Officer / SCM Head only."""

    def test_func(self):
        return roles.can_manage_purchase_orders(self.request.user)


def visible_indent_queryset(user, queryset):
    if roles.is_site_restricted(user):
        site = roles.user_site(user)
        return queryset.filter(site=site) if site else queryset.none()
    return queryset
