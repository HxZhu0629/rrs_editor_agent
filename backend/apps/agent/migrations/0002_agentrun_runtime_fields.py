from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agent', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentrun',
            name='status',
            field=models.CharField(
                choices=[
                    ('processing', 'Processing'),
                    ('waiting_approval', 'Waiting approval'),
                    ('success', 'Success'),
                    ('failed', 'Failed'),
                ],
                default='processing',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='agentrun',
            name='stop_reason',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='agentrun',
            name='turn_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='agentrun',
            name='tool_call_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
