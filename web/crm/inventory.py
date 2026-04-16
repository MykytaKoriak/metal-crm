from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    InventoryBalance,
    InventoryReceipt,
    InventoryReceiptItem,
    InventoryTransaction,
    Order,
    OrderItem,
    Product,
    ProductBOM,
    Task,
    Warehouse,
)


ZERO = Decimal("0")
THREE_DP = Decimal("0.001")
RESERVE_STATUSES = {Order.Status.IN_PROGRESS, Order.Status.IN_PRODUCTION}


def _quantize(value):
    if value is None:
        return ZERO
    return Decimal(value).quantize(THREE_DP)


def _display_quantity(value):
    return format(_quantize(value), "f")


def get_default_warehouse(warehouse_type):
    defaults = {
        Warehouse.WarehouseType.RAW: "Склад сировини",
        Warehouse.WarehouseType.WIP: "Склад НЗВ",
        Warehouse.WarehouseType.FINISHED: "Склад готової продукції",
    }
    warehouse = Warehouse.objects.filter(type=warehouse_type).order_by("id").first()
    if warehouse:
        return warehouse
    return Warehouse.objects.create(
        type=warehouse_type,
        name=defaults[warehouse_type],
        is_active=True,
    )


def ensure_default_warehouses():
    return {
        warehouse_type: get_default_warehouse(warehouse_type)
        for warehouse_type, _ in Warehouse.WarehouseType.choices
    }


def get_balance(product, warehouse, *, for_update=False):
    queryset = InventoryBalance.objects
    if for_update:
        queryset = queryset.select_for_update()
    balance, _ = queryset.get_or_create(product=product, warehouse=warehouse, defaults={"quantity": 0, "reserved_quantity": 0})
    return balance


@transaction.atomic
def create_inventory_transaction(
    *,
    transaction_type,
    product,
    quantity,
    warehouse_from=None,
    warehouse_to=None,
    order=None,
    order_item=None,
    production_stage=None,
    receipt=None,
    created_by=None,
):
    quantity = _quantize(quantity)
    if quantity <= 0:
        raise ValueError("Кількість має бути більшою за нуль.")

    if transaction_type == InventoryTransaction.TransactionType.IN:
        if warehouse_to is None:
            raise ValueError("Для приходу потрібно вказати склад призначення.")
        target_balance = get_balance(product, warehouse_to, for_update=True)
        target_balance.quantity = _quantize(target_balance.quantity + quantity)
        target_balance._inventory_service_operation = True
        target_balance.save()
    elif transaction_type == InventoryTransaction.TransactionType.OUT:
        if warehouse_from is None:
            raise ValueError("Для списання потрібно вказати склад джерело.")
        source_balance = get_balance(product, warehouse_from, for_update=True)
        if source_balance.available < quantity:
            raise ValueError("Недостатньо доступного залишку для списання.")
        source_balance.quantity = _quantize(source_balance.quantity - quantity)
        source_balance._inventory_service_operation = True
        source_balance.save()
    elif transaction_type == InventoryTransaction.TransactionType.MOVE:
        if warehouse_from is None or warehouse_to is None:
            raise ValueError("Для переміщення потрібно вказати склад джерело і склад призначення.")
        source_balance = get_balance(product, warehouse_from, for_update=True)
        if source_balance.available < quantity:
            raise ValueError("Недостатньо доступного залишку для переміщення.")
        target_balance = get_balance(product, warehouse_to, for_update=True)
        source_balance.quantity = _quantize(source_balance.quantity - quantity)
        target_balance.quantity = _quantize(target_balance.quantity + quantity)
        source_balance._inventory_service_operation = True
        target_balance._inventory_service_operation = True
        source_balance.save()
        target_balance.save()
    elif transaction_type == InventoryTransaction.TransactionType.RESERVE:
        if warehouse_from is None:
            raise ValueError("Для резерву потрібно вказати склад джерело.")
        source_balance = get_balance(product, warehouse_from, for_update=True)
        if source_balance.available < quantity:
            raise ValueError("Недостатньо доступного залишку для резерву.")
        source_balance.reserved_quantity = _quantize(source_balance.reserved_quantity + quantity)
        source_balance._inventory_service_operation = True
        source_balance.save()
    elif transaction_type == InventoryTransaction.TransactionType.RELEASE:
        if warehouse_from is None:
            raise ValueError("Для зняття резерву потрібно вказати склад джерело.")
        source_balance = get_balance(product, warehouse_from, for_update=True)
        if source_balance.reserved_quantity < quantity:
            raise ValueError("Неможливо зняти резерв більше за поточний резерв.")
        source_balance.reserved_quantity = _quantize(source_balance.reserved_quantity - quantity)
        source_balance._inventory_service_operation = True
        source_balance.save()
    else:
        raise ValueError("Непідтримуваний тип складської операції.")

    return InventoryTransaction.objects.create(
        type=transaction_type,
        product=product,
        quantity=quantity,
        warehouse_from=warehouse_from,
        warehouse_to=warehouse_to,
        order=order,
        order_item=order_item,
        production_stage=production_stage,
        receipt=receipt,
        created_by=created_by,
    )


@transaction.atomic
def post_inventory_receipt(
    *,
    warehouse,
    lines,
    supplier_name="",
    invoice_number="",
    document_date=None,
    comment="",
    created_by=None,
):
    active_lines = [
        line
        for line in lines
        if line.get("product") is not None and _quantize(line.get("quantity")) > 0
    ]
    if not active_lines:
        raise ValueError("Накладна має містити хоча б одну позицію матеріалу.")

    receipt = InventoryReceipt.objects.create(
        warehouse=warehouse,
        supplier_name=(supplier_name or "").strip(),
        invoice_number=(invoice_number or "").strip(),
        document_date=document_date or timezone.localdate(),
        comment=(comment or "").strip(),
        created_by=created_by,
        posted_at=timezone.now(),
        posted_by=created_by,
    )

    for line in active_lines:
        item = InventoryReceiptItem.objects.create(
            receipt=receipt,
            product=line["product"],
            quantity=_quantize(line["quantity"]),
            comment=(line.get("comment") or "").strip(),
        )
        create_inventory_transaction(
            transaction_type=InventoryTransaction.TransactionType.IN,
            product=item.product,
            quantity=item.quantity,
            warehouse_to=warehouse,
            receipt=receipt,
            created_by=created_by,
        )

    return receipt


def get_order_item_bom_rows(order_item):
    quantity = Decimal(order_item.quantity or 0)
    raw_warehouse = get_default_warehouse(Warehouse.WarehouseType.RAW)
    rows = []
    for bom in order_item.product.bom_items.select_related("material").order_by("material__name", "id"):
        required = _quantize(bom.quantity * quantity)
        balance = InventoryBalance.objects.filter(product=bom.material, warehouse=raw_warehouse).first()
        available = _quantize(balance.available if balance else 0)
        reserved = _quantize(
            InventoryTransaction.objects.filter(
                product=bom.material,
                order_item=order_item,
                type=InventoryTransaction.TransactionType.RESERVE,
            ).aggregate(total=Sum("quantity"))["total"] or 0
        ) - _quantize(
            InventoryTransaction.objects.filter(
                product=bom.material,
                order_item=order_item,
                type=InventoryTransaction.TransactionType.RELEASE,
            ).aggregate(total=Sum("quantity"))["total"] or 0
        )
        consumed = _quantize(
            InventoryTransaction.objects.filter(
                product=bom.material,
                order_item=order_item,
                type=InventoryTransaction.TransactionType.OUT,
            ).aggregate(total=Sum("quantity"))["total"] or 0
        )
        coverage = max(available, ZERO) + max(reserved, ZERO)
        shortage = max(required - coverage, ZERO)
        rows.append(
            {
                "material_id": bom.material_id,
                "material_name": bom.material.name,
                "material_sku": bom.material.sku or "",
                "unit": bom.material.unit or "шт",
                "required": required,
                "reserved": max(reserved, ZERO),
                "consumed": max(consumed, ZERO),
                "available": max(available, ZERO),
                "shortage": shortage,
                "is_enough": shortage <= 0,
                "warehouse_id": raw_warehouse.pk,
                "warehouse_name": raw_warehouse.name,
            }
        )
    return rows


def serialize_material_rows(rows):
    payload = []
    for row in rows:
        payload.append(
            {
                "material_id": row["material_id"],
                "material_name": row["material_name"],
                "material_sku": row["material_sku"],
                "unit": row["unit"],
                "required": _display_quantity(row["required"]),
                "reserved": _display_quantity(row["reserved"]),
                "consumed": _display_quantity(row["consumed"]),
                "available": _display_quantity(row["available"]),
                "shortage": _display_quantity(row["shortage"]),
                "is_enough": row["is_enough"],
                "warehouse_id": row["warehouse_id"],
                "warehouse_name": row["warehouse_name"],
            }
        )
    return payload


def refresh_order_item_materials(order_item, *, save=True):
    rows = get_order_item_bom_rows(order_item)
    required_materials = serialize_material_rows(rows)
    reserved_materials = [
        {
            "material_id": row["material_id"],
            "material_name": row["material_name"],
            "unit": row["unit"],
            "quantity": _display_quantity(row["reserved"]),
        }
        for row in rows
        if row["reserved"] > 0
    ]
    consumed_materials = [
        {
            "material_id": row["material_id"],
            "material_name": row["material_name"],
            "unit": row["unit"],
            "quantity": _display_quantity(row["consumed"]),
        }
        for row in rows
        if row["consumed"] > 0
    ]
    order_item.required_materials = required_materials
    order_item.reserved_materials = reserved_materials
    order_item.consumed_materials = consumed_materials
    if save:
        OrderItem.objects.filter(pk=order_item.pk).update(
            required_materials=required_materials,
            reserved_materials=reserved_materials,
            consumed_materials=consumed_materials,
        )
    return rows


def refresh_order_materials(order, *, save=True):
    rows_by_item = {}
    for order_item in order.items.select_related("product").all():
        rows_by_item[order_item.pk] = refresh_order_item_materials(order_item, save=save)
    return rows_by_item


def get_order_item_material_status(order_item):
    rows = get_order_item_bom_rows(order_item)
    if not rows:
        return "ok", rows
    enough_count = sum(1 for row in rows if row["is_enough"])
    if enough_count == len(rows):
        return "ok", rows
    if enough_count == 0:
        return "none", rows
    return "partial", rows


def _find_procurement_task(order):
    return (
        Task.objects.filter(order=order, title__startswith="Закупка матеріалів")
        .exclude(status=Task.Status.DONE)
        .order_by("date", "id")
        .first()
    )


def ensure_procurement_task(order, rows_by_item, *, created_by=None):
    shortages = []
    for order_item in order.items.select_related("product").all():
        rows = rows_by_item.get(order_item.pk) or get_order_item_bom_rows(order_item)
        missing_rows = [row for row in rows if row["shortage"] > 0]
        if not missing_rows:
            continue
        shortages.append((order_item, missing_rows))

    if not shortages:
        return None

    task = _find_procurement_task(order)
    lines = []
    for order_item, missing_rows in shortages:
        lines.append(f"{order_item.product.name} x {order_item.quantity}")
        for row in missing_rows:
            lines.append(
                f"- {row['material_name']}: бракує { _display_quantity(row['shortage']) } {row['unit']}"
            )

    description = "Потрібна закупівля матеріалів по замовленню.\n" + "\n".join(lines)
    deadline = order.deadline or timezone.localdate()
    if task:
        task.description = description
        task.comment = "Автоматично оновлено по дефіциту матеріалів."
        task._changed_by = created_by
        task.save(update_fields=["description", "comment"])
        return task

    assigned_user = order.manager
    return Task.objects.create(
        client=order.contact.client,
        contact=order.contact,
        order=order,
        title=f"Закупка матеріалів: {order.title or f'Замовлення #{order.pk}'}",
        description=description,
        assigned_by=created_by or assigned_user,
        assigned_to=assigned_user,
        date=deadline,
        status=Task.Status.NEW,
        comment="Автоматично створено через дефіцит матеріалів.",
    )


def _reconcile_order_item_reservations(order_item, *, created_by=None):
    raw_warehouse = get_default_warehouse(Warehouse.WarehouseType.RAW)
    rows = get_order_item_bom_rows(order_item)
    for row in rows:
        target_reserved = row["required"] if order_item.order.status in RESERVE_STATUSES else ZERO
        current_reserved = row["reserved"]
        material = Product.objects.get(pk=row["material_id"])
        if current_reserved < target_reserved:
            reserve_delta = min(target_reserved - current_reserved, row["available"])
            if reserve_delta > 0:
                create_inventory_transaction(
                    transaction_type=InventoryTransaction.TransactionType.RESERVE,
                    product=material,
                    quantity=reserve_delta,
                    warehouse_from=raw_warehouse,
                    order=order_item.order,
                    order_item=order_item,
                    created_by=created_by,
                )
        elif current_reserved > target_reserved:
            release_delta = current_reserved - target_reserved
            if release_delta > 0:
                create_inventory_transaction(
                    transaction_type=InventoryTransaction.TransactionType.RELEASE,
                    product=material,
                    quantity=release_delta,
                    warehouse_from=raw_warehouse,
                    order=order_item.order,
                    order_item=order_item,
                    created_by=created_by,
                )
    return refresh_order_item_materials(order_item, save=True)


@transaction.atomic
def reconcile_order_reservations(order, *, created_by=None):
    rows_by_item = {}
    for order_item in order.items.select_related("product").all():
        rows_by_item[order_item.pk] = _reconcile_order_item_reservations(order_item, created_by=created_by)
    ensure_procurement_task(order, rows_by_item, created_by=created_by)
    return rows_by_item


@transaction.atomic
def release_order_item_reservations(order_item, *, created_by=None):
    raw_warehouse = get_default_warehouse(Warehouse.WarehouseType.RAW)
    rows = get_order_item_bom_rows(order_item)
    for row in rows:
        if row["reserved"] <= 0:
            continue
        material = Product.objects.get(pk=row["material_id"])
        create_inventory_transaction(
            transaction_type=InventoryTransaction.TransactionType.RELEASE,
            product=material,
            quantity=row["reserved"],
            warehouse_from=raw_warehouse,
            order=order_item.order,
            order_item=order_item,
            created_by=created_by,
        )
    return rows


@transaction.atomic
def consume_materials_for_stage(stage, *, created_by=None):
    if stage.stage_type != stage.StageType.EXECUTION:
        return False

    raw_warehouse = get_default_warehouse(Warehouse.WarehouseType.RAW)
    order_item = stage.order_item
    rows = get_order_item_bom_rows(order_item)
    changed = False

    for row in rows:
        material = Product.objects.get(pk=row["material_id"])
        target_consumed = row["required"]
        already_consumed = row["consumed"]
        if already_consumed >= target_consumed:
            continue
        delta = target_consumed - already_consumed
        reserved_to_release = min(row["reserved"], delta)
        if reserved_to_release > 0:
            create_inventory_transaction(
                transaction_type=InventoryTransaction.TransactionType.RELEASE,
                product=material,
                quantity=reserved_to_release,
                warehouse_from=raw_warehouse,
                order=order_item.order,
                order_item=order_item,
                production_stage=stage,
                created_by=created_by,
            )
        create_inventory_transaction(
            transaction_type=InventoryTransaction.TransactionType.OUT,
            product=material,
            quantity=delta,
            warehouse_from=raw_warehouse,
            order=order_item.order,
            order_item=order_item,
            production_stage=stage,
            created_by=created_by,
        )
        changed = True

    if changed:
        refresh_order_item_materials(order_item, save=True)
    return changed


@transaction.atomic
def receive_finished_goods_for_stage(stage, *, created_by=None):
    if stage.stage_type != stage.StageType.READY_TO_SHIP:
        return False
    product = stage.order_item.product
    if not product.track_inventory or product.is_material:
        return False
    finished_warehouse = get_default_warehouse(Warehouse.WarehouseType.FINISHED)
    existing_quantity = _quantize(
        InventoryTransaction.objects.filter(
            type=InventoryTransaction.TransactionType.IN,
            product=product,
            order_item=stage.order_item,
            production_stage=stage,
            warehouse_to=finished_warehouse,
        ).aggregate(total=Sum("quantity"))["total"] or 0
    )
    target_quantity = _quantize(stage.order_item.quantity)
    if existing_quantity >= target_quantity:
        return False
    create_inventory_transaction(
        transaction_type=InventoryTransaction.TransactionType.IN,
        product=product,
        quantity=target_quantity - existing_quantity,
        warehouse_to=finished_warehouse,
        order=stage.order_item.order,
        order_item=stage.order_item,
        production_stage=stage,
        created_by=created_by,
    )
    return True


def can_plan_stage(stage):
    if stage.stage_type == stage.StageType.INTAKE:
        return True, "ok", []
    if stage.stage_type == stage.StageType.PROCUREMENT:
        status, rows = get_order_item_material_status(stage.order_item)
        return True, status, rows
    status, rows = get_order_item_material_status(stage.order_item)
    return status == "ok", status, rows


def get_inventory_product_rows(*, search="", warehouse_type="", shortage_only=False, product_kind=""):
    search = (search or "").strip()
    rows = []
    products = Product.objects.order_by("name")
    if product_kind == "materials":
        products = products.filter(is_material=True)
    elif product_kind == "products":
        products = products.filter(is_material=False)
    if search:
        products = products.filter(name__icontains=search) | products.filter(sku__icontains=search)
    for product in products.distinct():
        balances = product.inventory_balances.select_related("warehouse").order_by("warehouse__type", "warehouse__name")
        if warehouse_type:
            balances = balances.filter(warehouse__type=warehouse_type)
        total_quantity = _quantize(sum(balance.quantity for balance in balances))
        total_reserved = _quantize(sum(balance.reserved_quantity for balance in balances))
        available = total_quantity - total_reserved
        deficit = product.track_inventory and available < _quantize(product.min_stock_level)
        if shortage_only and not deficit:
            continue
        rows.append(
            {
                "product": product,
                "balances": list(balances),
                "total_quantity": total_quantity,
                "total_reserved": total_reserved,
                "available": available,
                "is_deficit": deficit,
            }
        )
    return rows


def get_product_transaction_rows(product, *, limit=100):
    return list(
        product.inventory_transactions.select_related(
            "warehouse_from",
            "warehouse_to",
            "order",
            "order_item",
            "production_stage",
            "created_by",
        ).order_by("-created_at", "-id")[:limit]
    )


def get_inventory_deficit_rows():
    return [row for row in get_inventory_product_rows(shortage_only=True) if row["is_deficit"]]


def get_procurement_user():
    user_model = get_user_model()
    return (
        user_model.objects.filter(profile__role__in=["sales_manager", "production"], is_active=True)
        .order_by("id")
        .first()
    )
