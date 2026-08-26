from decimal import Decimal


def machinery_utilization(date_from=None, date_to=None):
    """Utilisation (hours logged), fuel per hour, and hire cost by site
    for every machine with usage logs in the window."""
    from django.db.models import Sum

    from assets.models import Machine, MachineLog

    logs = MachineLog.objects.select_related("machine", "site")
    if date_from:
        logs = logs.filter(log_date__gte=date_from)
    if date_to:
        logs = logs.filter(log_date__lte=date_to)

    rows = []
    for machine in Machine.objects.all():
        machine_logs = logs.filter(machine=machine)
        totals = machine_logs.aggregate(hours=Sum("hours_run"), fuel=Sum("fuel_litres"))
        hours = totals["hours"] or Decimal("0")
        fuel = totals["fuel"] or Decimal("0")
        if hours == 0 and fuel == 0:
            continue
        fuel_per_hour = (fuel / hours) if hours else None
        hire_cost = None
        if machine.ownership == Machine.Ownership.HIRED and machine.hire_rate:
            if machine.rate_unit == Machine.RateUnit.PER_HOUR:
                hire_cost = hours * machine.hire_rate
        rows.append({
            "machine": machine, "hours_run": hours, "fuel_litres": fuel,
            "fuel_per_hour": fuel_per_hour, "hire_cost": hire_cost,
        })
    return rows
