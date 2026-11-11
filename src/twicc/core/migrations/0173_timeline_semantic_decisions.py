from django.db import migrations, models
import django.db.models.deletion
import django.db.models.functions.comparison


class Migration(migrations.Migration):
    dependencies = [("core", "0172_session_compressed_size")]

    operations = [
        migrations.CreateModel(
            name="TimelineDecision",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("provider", models.CharField(max_length=50)),
                ("storage_incarnation", models.CharField(max_length=36, null=True)),
                ("history_epoch", models.PositiveIntegerField(null=True)),
                ("run_id", models.UUIDField(null=True)),
                ("turn_id", models.UUIDField(null=True)),
                ("kind", models.CharField(max_length=10, choices=[("message", "Message"), ("ending", "Ending")])),
                ("identity_key", models.CharField(max_length=255)),
                ("occurred_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("not_before", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("payload", models.JSONField(default=dict)),
                ("status", models.CharField(max_length=12, default="pending", choices=[
                    ("pending", "Pending"), ("running", "Running"), ("accepted", "Accepted"),
                    ("rejected", "Rejected"), ("canceled", "Canceled"), ("invalidated", "Invalidated"),
                ])),
                ("reason", models.CharField(max_length=500, default="", blank=True)),
                ("requested_route", models.CharField(max_length=20, default="provider")),
                ("effective_route", models.CharField(max_length=20, null=True)),
                ("resolved_at", models.DateTimeField(null=True)),
                ("forced", models.BooleanField(default=False)),
                ("published_event_id", models.BigIntegerField(null=True)),
                ("applied_at", models.DateTimeField(null=True)),
                ("force_pending", models.BooleanField(default=False)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name="timeline_decisions", to="core.session")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["status", "not_before"], name="idx_decision_ready"),
                    models.Index(fields=["status", "expires_at"], name="idx_decision_expiry"),
                    models.Index(fields=["session", "turn_id"], name="idx_decision_session_turn"),
                ],
                "constraints": [models.UniqueConstraint(
                    models.F("session"), models.F("provider"),
                    django.db.models.functions.comparison.Coalesce("storage_incarnation", models.Value("")),
                    django.db.models.functions.comparison.Coalesce("history_epoch", models.Value(-1)),
                    models.F("kind"), models.F("identity_key"), name="uniq_timeline_decision_scope",
                )],
            },
        ),
    ]
