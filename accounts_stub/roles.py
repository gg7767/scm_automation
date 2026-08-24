SCM_HEAD = "SCM Head"
PURCHASE_OFFICER = "Purchase Officer (HO)"
SITE_MEMBER = "Site Member"
ACCOUNTS = "Accounts"
ADMIN = "Admin"

ALL_ROLES = [SCM_HEAD, PURCHASE_OFFICER, SITE_MEMBER, ACCOUNTS, ADMIN]


def in_group(user, *names):
    return user.groups.filter(name__in=names).exists()


def is_scm_head(user):
    return user.is_superuser or in_group(user, SCM_HEAD)


def can_manage_purchase_orders(user):
    """Create/edit lines, submit, send, cancel POs."""
    return user.is_superuser or in_group(user, SCM_HEAD, PURCHASE_OFFICER)


def can_view_purchase_orders(user):
    return user.is_superuser or in_group(user, SCM_HEAD, PURCHASE_OFFICER, ACCOUNTS, ADMIN, SITE_MEMBER)


def is_site_restricted(user):
    """True if this user's PO visibility is limited to their own site."""
    if user.is_superuser or in_group(user, SCM_HEAD, PURCHASE_OFFICER, ACCOUNTS, ADMIN):
        return False
    return in_group(user, SITE_MEMBER)


def user_site(user):
    profile = getattr(user, "profile", None)
    return profile.site if profile else None
