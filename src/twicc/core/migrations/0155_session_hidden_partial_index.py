# Django 6.1 compiles `hidden=False` on SQLite as `"hidden" = False` instead
# of `NOT "hidden"`. Without ANALYZE statistics, SQLite then picked the full
# index on `hidden` (it matches almost every row) over the project indexes.
# The full index becomes a partial one on the few hidden rows, and
# idx_session_visible is rebuilt so its condition is written the way 6.1
# writes the queries it serves (SQLite only uses a partial index when the
# query repeats its condition).

from django.db import migrations, models


def _full_hidden_indexes(schema_editor):
    """Names of the plain single-column indexes on core_session.hidden.

    The name depends on the database's history (table rebuilds rename it), so
    it is read from the schema instead of being hardcoded.
    """
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, 'core_session')
    return [
        name for name, info in constraints.items()
        if info['index'] and not info['unique'] and info['columns'] == ['hidden'] and name != 'idx_session_hidden'
    ]


# Both go through the editor's standard index operations (not raw SQL): the
# migration backend proves them, so no global foreign key check runs.
def drop_full_hidden_index(apps, schema_editor):
    Session = apps.get_model('core', 'Session')
    for name in _full_hidden_indexes(schema_editor):
        schema_editor.remove_index(Session, models.Index(fields=['hidden'], name=name))


def restore_full_hidden_index(apps, schema_editor):
    if _full_hidden_indexes(schema_editor):
        return
    Session = apps.get_model('core', 'Session')
    name = schema_editor._create_index_name(Session._meta.db_table, ['hidden'])
    schema_editor.add_index(Session, models.Index(fields=['hidden'], name=name))


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0154_inline_artifact_catalogs'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='session',
            name='idx_session_visible',
        ),
        # Dropping `db_index` through AlterField would rebuild the whole
        # core_session table on SQLite; only the index has to go.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='session',
                    name='hidden',
                    field=models.BooleanField(default=False),
                ),
            ],
            database_operations=[
                migrations.RunPython(drop_full_hidden_index, restore_full_hidden_index),
            ],
        ),
        migrations.AddIndex(
            model_name='session',
            index=models.Index(condition=models.Q(('hidden', True)), fields=['hidden'], name='idx_session_hidden'),
        ),
        migrations.AddIndex(
            model_name='session',
            index=models.Index(condition=models.Q(('created_at__isnull', False), ('hidden', False), ('type', 'session'), ('user_message_count__gt', 0)), fields=['project', '-mtime'], name='idx_session_visible'),
        ),
    ]
