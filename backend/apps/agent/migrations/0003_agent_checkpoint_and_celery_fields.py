from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agent', '0002_agentrun_runtime_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentconversation',
            name='title',
            field=models.CharField(blank=True, default='编辑助手', max_length=255),
        ),
        migrations.AddField(
            model_name='agentrun',
            name='celery_task_id',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='agentrun',
            name='pending_approval',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='agentrun',
            name='runtime_context',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='AgentCheckpoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('thread_id', models.CharField(db_index=True, max_length=64)),
                ('checkpoint_ns', models.CharField(blank=True, default='', max_length=255)),
                ('checkpoint_id', models.CharField(max_length=128)),
                ('parent_checkpoint_id', models.CharField(blank=True, default='', max_length=128)),
                ('checkpoint_type', models.CharField(max_length=64)),
                ('checkpoint_data', models.BinaryField()),
                ('metadata_type', models.CharField(max_length=64)),
                ('metadata_data', models.BinaryField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'agent_checkpoints',
                'ordering': ['-checkpoint_id'],
            },
        ),
        migrations.CreateModel(
            name='AgentCheckpointWrite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('thread_id', models.CharField(db_index=True, max_length=64)),
                ('checkpoint_ns', models.CharField(blank=True, default='', max_length=255)),
                ('checkpoint_id', models.CharField(max_length=128)),
                ('task_id', models.CharField(max_length=255)),
                ('write_index', models.IntegerField()),
                ('channel', models.CharField(max_length=255)),
                ('value_type', models.CharField(max_length=64)),
                ('value_data', models.BinaryField()),
                ('task_path', models.CharField(blank=True, default='', max_length=512)),
            ],
            options={
                'db_table': 'agent_checkpoint_writes',
            },
        ),
        migrations.AddConstraint(
            model_name='agentcheckpoint',
            constraint=models.UniqueConstraint(
                fields=('thread_id', 'checkpoint_ns', 'checkpoint_id'),
                name='unique_agent_checkpoint',
            ),
        ),
        migrations.AddConstraint(
            model_name='agentcheckpointwrite',
            constraint=models.UniqueConstraint(
                fields=('thread_id', 'checkpoint_ns', 'checkpoint_id', 'task_id', 'write_index'),
                name='unique_agent_checkpoint_write',
            ),
        ),
    ]
