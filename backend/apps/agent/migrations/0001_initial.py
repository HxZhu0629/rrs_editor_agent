import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('manuscripts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentConversation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(blank=True, default='编辑推荐助手', max_length=255)),
                ('status', models.CharField(choices=[('active', 'Active'), ('closed', 'Closed')], default='active', max_length=16)),
                ('context', models.JSONField(blank=True, default=dict)),
                (
                    'manuscript',
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='agent_conversations',
                        to='manuscripts.manuscript',
                    ),
                ),
            ],
            options={'db_table': 'agent_conversations', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='AgentMessage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('user', 'User'), ('assistant', 'Assistant'), ('tool', 'Tool'), ('system', 'System')], max_length=16)),
                ('content', models.TextField()),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'conversation',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='messages',
                        to='agent.agentconversation',
                    ),
                ),
            ],
            options={'db_table': 'agent_messages', 'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='AgentRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user_input', models.TextField()),
                ('status', models.CharField(choices=[('processing', 'Processing'), ('success', 'Success'), ('failed', 'Failed')], default='processing', max_length=16)),
                ('plan', models.JSONField(blank=True, default=dict)),
                ('tool_trace', models.JSONField(blank=True, default=list)),
                ('answer', models.TextField(blank=True, default='')),
                ('error_message', models.TextField(blank=True, default='')),
                (
                    'conversation',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='runs',
                        to='agent.agentconversation',
                    ),
                ),
            ],
            options={'db_table': 'agent_runs', 'ordering': ['-created_at']},
        ),
    ]
