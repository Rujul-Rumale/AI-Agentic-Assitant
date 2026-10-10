"""Task domain model and statuses (spec §31)."""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DISMISSED = "DISMISSED"


OPEN_STATUSES = {TaskStatus.PENDING, TaskStatus.ACKNOWLEDGED, TaskStatus.IN_PROGRESS}


def next_task_id(existing: list[str]) -> str:
    """Return the next unique TASK-xxxxx id given current task_ids."""
    highest = 0
    for tid in existing:
        try:
            highest = max(highest, int(tid.split("-")[-1]))
        except (ValueError, IndexError):
            continue
    return f"TASK-{highest + 1:05d}"