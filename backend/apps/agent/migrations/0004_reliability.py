from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('agent', '0003_agent_checkpoint_and_celery_fields')]

    operations = [
        migrations.CreateModel(name='AgentAdmission', fields=[('id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False))]),
        migrations.AddField(model_name='agentrun', name='request_id', field=models.CharField(blank=True, default='', max_length=64)),
        migrations.AddField(model_name='agentrun', name='trace_id', field=models.CharField(blank=True, default='', max_length=32)),
        migrations.AddField(model_name='agentrun', name='queued_at', field=models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
        migrations.AddField(model_name='agentrun', name='started_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='agentrun', name='finished_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='agentrun', name='lease_until', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='agentrun', name='lease_token', field=models.CharField(blank=True, default='', max_length=32)),
        migrations.AddField(model_name='agentrun', name='queue_wait_seconds', field=models.FloatField(blank=True, null=True)),
        migrations.AddField(model_name='agentrun', name='execution_seconds', field=models.FloatField(blank=True, null=True)),
        migrations.AddIndex(model_name='agentrun', index=models.Index(fields=['status', 'queued_at'], name='agent_status_queue_idx')),
    ]
