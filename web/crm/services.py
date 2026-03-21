from django.db.models import Q


def build_order_title_from_items(order) -> str:
    names = list(
        order.items.select_related("product").values_list("product__name", flat=True)
    )
    seen = set()
    unique_names = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            unique_names.append(name)
    return ", ".join(unique_names)


def sync_order_title(order, *, save=True) -> str:
    new_title = build_order_title_from_items(order)
    if new_title != (order.title or ""):
        order.title = new_title
        if save:
            order.save(update_fields=["title"])
    return order.title


def derive_order_status_from_production(order, *, preserve_terminal=True) -> str:
    from manufacture.models import ProductionSlot, ProductionStage

    if preserve_terminal and order.status in {order.Status.COMPLETED, order.Status.CANCELED}:
        return order.status

    if not order.items.exists():
        return order.Status.NEW

    stages = ProductionStage.objects.filter(order_item__order=order)
    if not stages.exists():
        if order.status in {order.Status.NEW, order.Status.IN_PROGRESS}:
            return order.status
        return order.Status.IN_PROGRESS

    total_stages = stages.count()
    done_stages = stages.filter(status=ProductionStage.Status.DONE).count()
    has_started_production = (
        done_stages > 0
        or stages.filter(
            status__in=[
                ProductionStage.Status.SCHEDULED,
                ProductionStage.Status.IN_PROGRESS,
                ProductionStage.Status.BLOCKED,
            ]
        ).exists()
        or stages.filter(Q(planned_start__isnull=False) | Q(planned_end__isnull=False)).exists()
        or ProductionSlot.objects.filter(order=order).exists()
    )

    if total_stages and done_stages == total_stages:
        return order.Status.READY

    if has_started_production:
        return order.Status.IN_PRODUCTION

    if order.status in {order.Status.NEW, order.Status.IN_PROGRESS}:
        return order.status

    return order.Status.IN_PROGRESS


def sync_order_status_from_production(order, *, save=True, preserve_terminal=True) -> str:
    if not getattr(order, "pk", None) or not order.__class__.objects.filter(pk=order.pk).exists():
        return getattr(order, "status", "")

    new_status = derive_order_status_from_production(order, preserve_terminal=preserve_terminal)
    if new_status != order.status:
        order.status = new_status
        if save:
            order.save(update_fields=["status"])
    return order.status
