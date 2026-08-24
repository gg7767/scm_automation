from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts_stub import roles


@login_required
def home(request):
    context = {}
    if roles.can_view_purchase_orders(request.user):
        from purchase.services import (
            overdue_purchase_orders,
            pending_approvals_for_user,
            recent_purchase_orders,
            this_month_stats,
        )

        context.update({
            "show_dashboard": True,
            "pending_approvals": pending_approvals_for_user(request.user),
            "recent_pos": recent_purchase_orders(request.user),
            "month_stats": this_month_stats(request.user),
            "overdue_pos": overdue_purchase_orders(request.user),
        })
    return render(request, "home.html", context)
