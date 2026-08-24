from masters.permissions import MASTERS_MANAGER_GROUPS


def roles(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"is_masters_manager": False}
    is_masters_manager = user.is_superuser or user.groups.filter(name__in=MASTERS_MANAGER_GROUPS).exists()
    return {"is_masters_manager": is_masters_manager}
