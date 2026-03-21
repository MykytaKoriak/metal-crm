from django.db import migrations, models
from django.db.models import Q


def sync_order_workflow_statuses(apps, schema_editor):
    Order = apps.get_model("crm", "Order")
    OrderItem = apps.get_model("crm", "OrderItem")
    ProductionSlot = apps.get_model("manufacture", "ProductionSlot")
    ProductionStage = apps.get_model("manufacture", "ProductionStage")

    terminal_statuses = {"completed", "canceled"}
    active_stage_statuses = {"scheduled", "in_progress", "blocked"}

    for order in Order.objects.all():
        current_status = "ready" if order.status == "shipped" else order.status

        if current_status in terminal_statuses:
            if current_status != order.status:
                order.status = current_status
                order.save(update_fields=["status"])
            continue

        if not OrderItem.objects.filter(order_id=order.id).exists():
            new_status = "new"
        else:
            stages = ProductionStage.objects.filter(order_item__order_id=order.id)
            if not stages.exists():
                new_status = current_status if current_status in {"new", "in_progress"} else "in_progress"
            else:
                total_stages = stages.count()
                done_stages = stages.filter(status="done").count()
                has_started_production = (
                    done_stages > 0
                    or stages.filter(status__in=active_stage_statuses).exists()
                    or stages.filter(Q(planned_start__isnull=False) | Q(planned_end__isnull=False)).exists()
                    or ProductionSlot.objects.filter(order_id=order.id).exists()
                )

                if total_stages and done_stages == total_stages:
                    new_status = "ready"
                elif has_started_production:
                    new_status = "in_production"
                elif current_status in {"new", "in_progress"}:
                    new_status = current_status
                else:
                    new_status = "in_progress"

        if new_status != order.status:
            order.status = new_status
            order.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0004_order_manager"),
        ("manufacture", "0002_alter_machine_type_alter_machine_workday_end_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "Новий"),
                    ("in_progress", "В роботі"),
                    ("in_production", "В виробництві"),
                    ("ready", "Готовий"),
                    ("completed", "Завершений"),
                    ("canceled", "Скасований"),
                ],
                db_index=True,
                default="new",
                max_length=20,
                verbose_name="Статус",
            ),
        ),
        migrations.RunPython(sync_order_workflow_statuses, migrations.RunPython.noop),
    ]
