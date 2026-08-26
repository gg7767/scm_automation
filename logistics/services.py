def freight_by_site_transporter_month():
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth

    from logistics.models import TransportTrip

    rows = (
        TransportTrip.objects.annotate(month=TruncMonth("trip_date"))
        .values("to_site__code", "transporter__name", "month")
        .annotate(total_freight=Sum("freight_amount"))
        .order_by("month", "to_site__code")
    )
    return [
        {
            "site": r["to_site__code"], "transporter": r["transporter__name"],
            "month": r["month"].strftime("%b %Y"), "total_freight": r["total_freight"],
        }
        for r in rows
    ]
