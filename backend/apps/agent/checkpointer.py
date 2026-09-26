from threading import RLock, current_thread, main_thread
from typing import Any, Iterator, Optional, Sequence

from django.db import connections, transaction
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)

from .models import AgentCheckpoint, AgentCheckpointWrite


def _config(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> dict:
    return {
        'configurable': {
            'thread_id': thread_id,
            'checkpoint_ns': checkpoint_ns,
            'checkpoint_id': checkpoint_id,
        },
    }


def _close_worker_connections() -> None:
    if current_thread() is not main_thread():
        connections.close_all()


class DjangoCheckpointSaver(BaseCheckpointSaver):
    """Persist LangGraph checkpoints in the project's configured Django database."""

    def __init__(self, *, serde=None):
        super().__init__(serde=serde)
        self._lock = RLock()

    def _row(self, config) -> Optional[AgentCheckpoint]:
        values = config['configurable']
        queryset = AgentCheckpoint.objects.filter(
            thread_id=str(values['thread_id']),
            checkpoint_ns=str(values.get('checkpoint_ns', '')),
        )
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            return queryset.filter(checkpoint_id=checkpoint_id).first()
        return queryset.order_by('-checkpoint_id').first()

    def _tuple(self, row: AgentCheckpoint) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((row.checkpoint_type, bytes(row.checkpoint_data)))
        metadata = self.serde.loads_typed((row.metadata_type, bytes(row.metadata_data)))
        pending_writes = [
            (write.task_id, write.channel, self.serde.loads_typed((write.value_type, bytes(write.value_data))))
            for write in AgentCheckpointWrite.objects.filter(
                thread_id=row.thread_id,
                checkpoint_ns=row.checkpoint_ns,
                checkpoint_id=row.checkpoint_id,
            ).order_by('id')
        ]
        parent_config = (
            _config(row.thread_id, row.checkpoint_ns, row.parent_checkpoint_id)
            if row.parent_checkpoint_id else None
        )
        return CheckpointTuple(
            config=_config(row.thread_id, row.checkpoint_ns, row.checkpoint_id),
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def get_tuple(self, config) -> Optional[CheckpointTuple]:
        try:
            with self._lock:
                row = self._row(config)
                return self._tuple(row) if row else None
        finally:
            _close_worker_connections()

    def list(
        self,
        config,
        *,
        filter: Optional[dict[str, Any]] = None,
        before=None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        queryset = AgentCheckpoint.objects.all()
        if config:
            values = config['configurable']
            queryset = queryset.filter(thread_id=str(values['thread_id']))
            if 'checkpoint_ns' in values:
                queryset = queryset.filter(checkpoint_ns=str(values.get('checkpoint_ns', '')))
            if checkpoint_id := get_checkpoint_id(config):
                queryset = queryset.filter(checkpoint_id=checkpoint_id)
        if before and (before_id := get_checkpoint_id(before)):
            queryset = queryset.filter(checkpoint_id__lt=before_id)
        try:
            matched = 0
            for row in queryset.order_by('-checkpoint_id').iterator():
                item = self._tuple(row)
                if filter and not all(item.metadata.get(key) == value for key, value in filter.items()):
                    continue
                if limit is not None and matched >= limit:
                    break
                matched += 1
                yield item
        finally:
            _close_worker_connections()

    def put(
        self,
        config,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions,
    ):
        try:
            with self._lock:
                values = config['configurable']
                checkpoint_type, checkpoint_data = self.serde.dumps_typed(checkpoint)
                metadata_type, metadata_data = self.serde.dumps_typed(metadata)
                AgentCheckpoint.objects.update_or_create(
                    thread_id=str(values['thread_id']),
                    checkpoint_ns=str(values.get('checkpoint_ns', '')),
                    checkpoint_id=str(checkpoint['id']),
                    defaults={
                        'parent_checkpoint_id': str(values.get('checkpoint_id', '')),
                        'checkpoint_type': checkpoint_type,
                        'checkpoint_data': checkpoint_data,
                        'metadata_type': metadata_type,
                        'metadata_data': metadata_data,
                    },
                )
                return _config(
                    str(values['thread_id']),
                    str(values.get('checkpoint_ns', '')),
                    str(checkpoint['id']),
                )
        finally:
            _close_worker_connections()

    def put_writes(
        self,
        config,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = '',
    ) -> None:
        try:
            with self._lock:
                values = config['configurable']
                with transaction.atomic():
                    for index, (channel, value) in enumerate(writes):
                        write_index = WRITES_IDX_MAP.get(channel, index)
                        value_type, value_data = self.serde.dumps_typed(value)
                        lookup = {
                            'thread_id': str(values['thread_id']),
                            'checkpoint_ns': str(values.get('checkpoint_ns', '')),
                            'checkpoint_id': str(values['checkpoint_id']),
                            'task_id': task_id,
                            'write_index': write_index,
                        }
                        defaults = {
                            'channel': channel,
                            'value_type': value_type,
                            'value_data': value_data,
                            'task_path': task_path,
                        }
                        if write_index < 0:
                            AgentCheckpointWrite.objects.update_or_create(**lookup, defaults=defaults)
                        else:
                            AgentCheckpointWrite.objects.get_or_create(**lookup, defaults=defaults)
        finally:
            _close_worker_connections()

    def delete_thread(self, thread_id: str) -> None:
        try:
            with self._lock:
                with transaction.atomic():
                    AgentCheckpointWrite.objects.filter(thread_id=str(thread_id)).delete()
                    AgentCheckpoint.objects.filter(thread_id=str(thread_id)).delete()
        finally:
            _close_worker_connections()
