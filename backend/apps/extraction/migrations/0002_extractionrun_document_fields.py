from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('extraction', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='extractionrun',
            name='parser_name',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='extractionrun',
            name='document_markdown',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='extractionrun',
            name='document_blocks',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='extractionrun',
            name='document_quality',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
