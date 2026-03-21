import django.db.models.deletion
from django.db import migrations, models


def forwards_fill_task_workflow(apps, schema_editor):
    Task = apps.get_model("crm", "Task")

    for task in Task.objects.select_related("contact"):
        task.client_id = task.contact.client_id if task.contact_id else None
        task.workflow_status = "done" if task.status else "new"
        task.save(update_fields=["client", "workflow_status"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0005_order_status_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tasks",
                to="crm.client",
                verbose_name="Клієнт",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="crm.order",
                verbose_name="Замовлення",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="workflow_status",
            field=models.CharField(
                choices=[
                    ("new", "Нова"),
                    ("in_progress", "В роботі"),
                    ("waiting", "Очікує"),
                    ("done", "Виконано"),
                ],
                db_index=True,
                default="new",
                max_length=20,
                verbose_name="Статус",
            ),
        ),
        migrations.RunPython(forwards_fill_task_workflow, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="task",
            name="contact",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="crm.contact",
                verbose_name="Контакт",
            ),
        ),
        migrations.RemoveField(
            model_name="task",
            name="status",
        ),
        migrations.RenameField(
            model_name="task",
            old_name="workflow_status",
            new_name="status",
        ),
        migrations.AlterField(
            model_name="task",
            name="client",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tasks",
                to="crm.client",
                verbose_name="Клієнт",
            ),
        ),
        migrations.AlterField(
            model_name="task",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "Нова"),
                    ("in_progress", "В роботі"),
                    ("waiting", "Очікує"),
                    ("done", "Виконано"),
                ],
                db_index=True,
                default="new",
                max_length=20,
                verbose_name="Статус",
            ),
        ),
    ]
