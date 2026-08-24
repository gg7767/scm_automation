from accounts_stub import roles as role_helpers
from masters.permissions import MASTERS_MANAGER_GROUPS


def roles(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "is_masters_manager": False, "can_view_po": False, "can_manage_po": False,
            "can_view_vendor_ledgers": False,
        }
    is_masters_manager = user.is_superuser or user.groups.filter(name__in=MASTERS_MANAGER_GROUPS).exists()
    return {
        "is_masters_manager": is_masters_manager,
        "can_view_po": role_helpers.can_view_purchase_orders(user),
        "can_manage_po": role_helpers.can_manage_purchase_orders(user),
        "can_view_vendor_ledgers": (
            role_helpers.can_view_purchase_orders(user) and not role_helpers.is_site_restricted(user)
        ),
    }
