from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional

import config
from db.shelf_ops import get_all_compartments, store_book, take_book_by_cid


REG_NEW_CMD_FLAG = 0
REG_CMD = 1
REG_FLOOR_ID = 2
REG_CELL_ID = 3
REG_ACK = 4
REGISTER_COUNT = 5

CMD_FETCH_BOOK = 0x01
CMD_STORE_BOOK = 0x02

ACK_OK = 0x00
ACK_BUSY = 0x01
ACK_PARAM_ERR = 0x02
ACK_FAULT = 0x03
ACK_UNKNOWN_CMD = 0x04
ACK_I2C_ERR = 0x05

# Local-only sentinel used by the Raspberry Pi to distinguish
# "not processed yet" from ACK_OK (0x00).
ACK_PENDING = 0xFF

ACK_LABELS = {
    ACK_OK: "ok",
    ACK_BUSY: "busy",
    ACK_PARAM_ERR: "param_err",
    ACK_FAULT: "fault",
    ACK_UNKNOWN_CMD: "unknown_cmd",
    ACK_I2C_ERR: "i2c_err",
    ACK_PENDING: "pending",
}

ACK_MESSAGES = {
    ACK_OK: "controller accepted command",
    ACK_BUSY: "controller is busy",
    ACK_PARAM_ERR: "controller rejected command parameters",
    ACK_FAULT: "controller is in fault state",
    ACK_UNKNOWN_CMD: "controller reported unknown command",
    ACK_I2C_ERR: "controller reported i2c error",
    ACK_PENDING: "controller has not processed the command yet",
}


def motion_mode():
    mode = (getattr(config, "MOTION_MODE", "direct") or "direct").strip().lower()
    if mode not in {"direct", "dispatch", "dispatch_and_commit"}:
        return "direct"
    return mode


def dispatch_enabled():
    return motion_mode() in {"dispatch", "dispatch_and_commit"}


def commit_on_ack():
    return motion_mode() == "dispatch_and_commit" or bool(
        getattr(config, "STM32_ACK_IMPLIES_COMPLETION", False)
    )


def _ack_label(ack):
    return ACK_LABELS.get(ack, "unknown")


def _ack_message(ack):
    return ACK_MESSAGES.get(ack, "controller returned unknown ack")


class MotionProtocolError(RuntimeError):
    def __init__(self, message, task=None):
        super().__init__(message)
        self.task = task


class MotionBusyError(MotionProtocolError):
    pass


class MotionAckTimeoutError(MotionProtocolError):
    pass


@dataclass
class MotionTask:
    task_id: str
    action: str
    cmd: int
    cid: int
    floor_id: int
    cell_id: int
    title: str = ""
    book_id: Optional[int] = None
    user_id: Optional[int] = None
    ack: Optional[int] = None
    ack_label: str = ""
    status: str = "queued"
    message: str = ""
    pending_commit: bool = False
    committed: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def to_dict(self):
        return asdict(self)


class RegisterBank:
    def __init__(self):
        self._lock = threading.Lock()
        self._registers = [0] * REGISTER_COUNT
        self._registers[REG_ACK] = ACK_PENDING

    def reset(self):
        with self._lock:
            self._registers = [0] * REGISTER_COUNT
            self._registers[REG_ACK] = ACK_PENDING

    def prepare_command(self, cmd, floor_id, cell_id):
        with self._lock:
            self._registers[REG_ACK] = ACK_PENDING
            self._registers[REG_CMD] = cmd & 0xFF
            self._registers[REG_FLOOR_ID] = floor_id & 0xFF
            self._registers[REG_CELL_ID] = cell_id & 0xFF
            self._registers[REG_NEW_CMD_FLAG] = 1

    def controller_write_ack(self, ack, clear_flag=True):
        with self._lock:
            self._registers[REG_ACK] = ack & 0xFF
            if clear_flag:
                self._registers[REG_NEW_CMD_FLAG] = 0

    def snapshot(self):
        with self._lock:
            return list(self._registers)


_state_lock = threading.Lock()
_register_bank = RegisterBank()
_tasks = {}
_task_order = []
_active_task_id = None
_backend_started = False


def initialize_motion_link():
    global _backend_started
    with _state_lock:
        if _backend_started:
            return get_motion_status()
        _backend_started = True
    return get_motion_status()


def reset_motion_state():
    global _active_task_id, _backend_started
    with _state_lock:
        _tasks.clear()
        _task_order[:] = []
        _active_task_id = None
        _backend_started = False
        _register_bank.reset()


def _trim_task_history():
    while len(_task_order) > 30:
        stale_id = _task_order.pop(0)
        if stale_id != _active_task_id:
            _tasks.pop(stale_id, None)


def _mark_active_task(task_id):
    global _active_task_id
    _active_task_id = task_id


def _clear_active_task(task_id):
    global _active_task_id
    if _active_task_id == task_id:
        _active_task_id = None


def _current_active_task():
    if not _active_task_id:
        return None
    return _tasks.get(_active_task_id)


def _lookup_compartment(cid):
    cid = int(cid)
    for raw_cid, floor_hint, cell_hint, _status in get_all_compartments():
        if int(raw_cid) != cid:
            continue
        floor_id = int(floor_hint)
        cell_id = int(cell_hint) - 1 + int(getattr(config, "STM32_CELL_ID_BASE", 0))
        if cell_id < 0:
            raise MotionProtocolError("invalid cell mapping", task={"cid": cid})
        return {
            "cid": cid,
            "floor_id": floor_id,
            "cell_id": cell_id,
        }
    raise MotionProtocolError("compartment not found", task={"cid": cid})


def _create_task(action, cid, title="", book_id=None, user_id=None):
    mapping = _lookup_compartment(cid)
    if action == "take":
        cmd = CMD_FETCH_BOOK
    elif action == "store":
        cmd = CMD_STORE_BOOK
    else:
        raise MotionProtocolError("unsupported action", task={"action": action})

    task = MotionTask(
        task_id=uuid.uuid4().hex[:12],
        action=action,
        cmd=cmd,
        cid=mapping["cid"],
        floor_id=mapping["floor_id"],
        cell_id=mapping["cell_id"],
        title=(title or "").strip(),
        book_id=book_id,
        user_id=user_id,
        ack=ACK_PENDING,
        ack_label=_ack_label(ACK_PENDING),
        status="waiting_ack",
        message=_ack_message(ACK_PENDING),
    )
    return task


def _wait_for_ack():
    timeout_ms = max(1, int(getattr(config, "STM32_ACK_TIMEOUT_MS", 3000)))
    poll_ms = max(1, int(getattr(config, "STM32_POLL_INTERVAL_MS", 50)))
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        snapshot = _register_bank.snapshot()
        if snapshot[REG_NEW_CMD_FLAG] == 0 and snapshot[REG_ACK] != ACK_PENDING:
            return snapshot[REG_ACK], snapshot
        time.sleep(poll_ms / 1000.0)
    return None, _register_bank.snapshot()


def _finalize_task_record(task, ack, message, status, pending_commit=False, committed=False):
    task.ack = ack
    task.ack_label = _ack_label(ack)
    task.message = message
    task.status = status
    task.pending_commit = pending_commit
    task.committed = committed
    task.updated_at = time.time()
    if status in {"completed", "failed", "rejected", "ack_timeout"}:
        task.completed_at = task.updated_at
    return task


def _apply_commit(task):
    if task.committed:
        return

    if task.action == "store":
        if task.book_id is None:
            raise MotionProtocolError("store task missing book_id", task=task.to_dict())
        store_book(task.book_id, task.cid, user_id=task.user_id)
    elif task.action == "take":
        take_book_by_cid(task.cid, user_id=task.user_id)
    else:
        raise MotionProtocolError("unsupported commit action", task=task.to_dict())

    task.committed = True
    task.pending_commit = False
    task.updated_at = time.time()
    task.completed_at = task.updated_at


def dispatch_motion_task(action, cid, title="", book_id=None, user_id=None):
    initialize_motion_link()
    with _state_lock:
        active_task = _current_active_task()
        if active_task and active_task.status in {"waiting_ack", "accepted"}:
            raise MotionBusyError("another motion task is still active", task=active_task.to_dict())

        task = _create_task(action, cid, title=title, book_id=book_id, user_id=user_id)
        _tasks[task.task_id] = task
        _task_order.append(task.task_id)
        _trim_task_history()
        _mark_active_task(task.task_id)
        _register_bank.prepare_command(task.cmd, task.floor_id, task.cell_id)

    ack, _snapshot = _wait_for_ack()
    with _state_lock:
        stored_task = _tasks.get(task.task_id, task)
        if ack is None:
            _finalize_task_record(
                stored_task,
                ACK_PENDING,
                "controller did not acknowledge command in time",
                "ack_timeout",
            )
            _clear_active_task(stored_task.task_id)
            raise MotionAckTimeoutError(stored_task.message, task=stored_task.to_dict())

        if ack != ACK_OK:
            _finalize_task_record(
                stored_task,
                ack,
                _ack_message(ack),
                "rejected",
            )
            _clear_active_task(stored_task.task_id)
            raise MotionProtocolError(stored_task.message, task=stored_task.to_dict())

        if commit_on_ack():
            _apply_commit(stored_task)
            _finalize_task_record(
                stored_task,
                ack,
                "command accepted and inventory updated",
                "completed",
                pending_commit=False,
                committed=True,
            )
            _clear_active_task(stored_task.task_id)
        else:
            _finalize_task_record(
                stored_task,
                ack,
                "command accepted; waiting for execution confirmation",
                "accepted",
                pending_commit=True,
                committed=False,
            )

        return stored_task.to_dict()


def complete_motion_task(task_id, success=True, error_message=""):
    with _state_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise MotionProtocolError("task not found", task={"task_id": task_id})

        if task.status == "completed":
            return task.to_dict()

        if not success:
            _finalize_task_record(
                task,
                task.ack if task.ack is not None else ACK_PENDING,
                (error_message or "execution failed").strip(),
                "failed",
                pending_commit=False,
                committed=False,
            )
            _clear_active_task(task.task_id)
            return task.to_dict()

        _apply_commit(task)
        _finalize_task_record(
            task,
            task.ack if task.ack is not None else ACK_OK,
            "execution confirmed and inventory updated",
            "completed",
            pending_commit=False,
            committed=True,
        )
        _clear_active_task(task.task_id)
        return task.to_dict()


def mock_controller_ack(ack=ACK_OK, clear_flag=True):
    ack = int(ack)
    if ack not in ACK_LABELS:
        raise MotionProtocolError("unsupported mock ack", task={"ack": ack})
    _register_bank.controller_write_ack(ack, clear_flag=clear_flag)
    active_task = _current_active_task()
    return active_task.to_dict() if active_task else None


def get_motion_task(task_id):
    task = _tasks.get(task_id)
    return task.to_dict() if task else None


def get_motion_status():
    active_task = _current_active_task()
    recent_tasks = []
    for task_id in reversed(_task_order[-10:]):
        task = _tasks.get(task_id)
        if task is not None:
            recent_tasks.append(task.to_dict())

    return {
        "mode": motion_mode(),
        "dispatch_enabled": dispatch_enabled(),
        "commit_on_ack": commit_on_ack(),
        "backend": getattr(config, "STM32_PROTOCOL_BACKEND", "memory"),
        "slave_addr": getattr(config, "STM32_I2C_SLAVE_ADDR", 0x30),
        "registers": {
            "reg0": _register_bank.snapshot()[REG_NEW_CMD_FLAG],
            "reg1": _register_bank.snapshot()[REG_CMD],
            "reg2": _register_bank.snapshot()[REG_FLOOR_ID],
            "reg3": _register_bank.snapshot()[REG_CELL_ID],
            "reg4": _register_bank.snapshot()[REG_ACK],
        },
        "active_task": active_task.to_dict() if active_task else None,
        "recent_tasks": recent_tasks,
    }
