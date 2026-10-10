"""Explicit permission model (spec §54).

Default policy:
  MONITOR_PRIVATE  = ALLOW
  MONITOR_GROUP    = ALLOW
  CLASSIFY_MESSAGE = ALLOW
  CREATE_TASK      = ALLOW
  NOTIFY_SELF      = ALLOW
  ACK_PRIVATE      = ALLOW
  SEND_PRIVATE     = MANUAL_ONLY   (automatic acks are the only auto send)
  SEND_GROUP       = DENY
  MODIFY_GROUP     = DENY
"""

from __future__ import annotations

from enum import Enum


class Permission(str, Enum):
    MONITOR_PRIVATE = "MONITOR_PRIVATE"
    MONITOR_GROUP = "MONITOR_GROUP"
    CLASSIFY_MESSAGE = "CLASSIFY_MESSAGE"
    CREATE_TASK = "CREATE_TASK"
    NOTIFY_SELF = "NOTIFY_SELF"
    ACK_PRIVATE = "ACK_PRIVATE"
    SEND_PRIVATE = "SEND_PRIVATE"
    SEND_GROUP = "SEND_GROUP"
    MODIFY_GROUP = "MODIFY_GROUP"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    DRY_RUN = "DRY_RUN"


DEFAULT_POLICY: dict[Permission, Decision] = {
    Permission.MONITOR_PRIVATE: Decision.ALLOW,
    Permission.MONITOR_GROUP: Decision.ALLOW,
    Permission.CLASSIFY_MESSAGE: Decision.ALLOW,
    Permission.CREATE_TASK: Decision.ALLOW,
    Permission.NOTIFY_SELF: Decision.ALLOW,
    Permission.ACK_PRIVATE: Decision.ALLOW,
    Permission.SEND_PRIVATE: Decision.DENY,   # only auto acks are permitted sends
    Permission.SEND_GROUP: Decision.DENY,
    Permission.MODIFY_GROUP: Decision.DENY,
}