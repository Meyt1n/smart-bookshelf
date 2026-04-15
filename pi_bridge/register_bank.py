from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class RegisterBusyError(RuntimeError):
    pass


class MemoryRegisterBank:
    def __init__(self, *, mirror_path: str | None = None):
        self._lock = threading.Lock()
        self._registers = {index: 0 for index in range(5)}
        self._last_command = None
        self._mirror_path = Path(mirror_path).expanduser().resolve() if mirror_path else None
        self._write_mirror()

    def snapshot(self):
        with self._lock:
            return self._snapshot_unlocked()

    def size(self) -> int:
        return 5

    def read_window(self, start_register: int = 0, count: int | None = None) -> bytes:
        with self._lock:
            start = max(0, int(start_register))
            if count is None:
                count = self.size() - start
            count = max(0, int(count))
            values = []
            for register in range(start, min(self.size(), start + count)):
                values.append(int(self._registers.get(register, 0)) & 0xFF)
            return bytes(values)

    def write_registers(self, start_register: int, values) -> dict:
        with self._lock:
            start = int(start_register)
            if start < 0:
                raise ValueError("register index must be >= 0")

            for offset, value in enumerate(values):
                register = start + offset
                if register >= self.size():
                    break
                self._registers[register] = int(value) & 0xFF
            snapshot = self._snapshot_unlocked()

        self._write_mirror()
        return snapshot

    def stage_command(self, *, cmd: int, floor_id: int, cell_id: int, cid=None, title=""):
        with self._lock:
            if int(self._registers[0]) != 0:
                raise RegisterBusyError("reg0 is still 1, previous command has not been consumed")

            self._registers[4] = 0
            self._registers[1] = int(cmd)
            self._registers[2] = int(floor_id)
            self._registers[3] = int(cell_id)
            self._registers[0] = 1
            self._last_command = {
                "cmd": int(cmd),
                "floor_id": int(floor_id),
                "cell_id": int(cell_id),
                "cid": cid,
                "title": title or "",
                "staged_at": time.time(),
            }
            snapshot = self._snapshot_unlocked()

        self._write_mirror()
        return snapshot

    def set_last_command(self, *, cmd: int, floor_id: int, cell_id: int, cid=None, title=""):
        with self._lock:
            self._last_command = {
                "cmd": int(cmd),
                "floor_id": int(floor_id),
                "cell_id": int(cell_id),
                "cid": cid,
                "title": title or "",
                "staged_at": time.time(),
            }
            snapshot = self._snapshot_unlocked()

        self._write_mirror()
        return snapshot

    def load_snapshot(self, registers, *, last_command=None):
        with self._lock:
            if isinstance(registers, dict):
                for index in range(5):
                    key = f"reg{index}"
                    self._registers[index] = int(registers.get(key, 0)) & 0xFF
            else:
                values = list(registers)
                for index in range(5):
                    self._registers[index] = int(values[index] if index < len(values) else 0) & 0xFF

            if last_command is not None:
                self._last_command = last_command
            snapshot = self._snapshot_unlocked()

        self._write_mirror()
        return snapshot

    def reset(self):
        with self._lock:
            for index in range(5):
                self._registers[index] = 0
            self._last_command = None
            snapshot = self._snapshot_unlocked()

        self._write_mirror()
        return snapshot

    def _snapshot_unlocked(self):
        return {
            "registers": {
                "reg0": int(self._registers[0]),
                "reg1": int(self._registers[1]),
                "reg2": int(self._registers[2]),
                "reg3": int(self._registers[3]),
                "reg4": int(self._registers[4]),
            },
            "last_command": self._last_command,
        }

    def _write_mirror(self):
        if not self._mirror_path:
            return

        self._mirror_path.parent.mkdir(parents=True, exist_ok=True)
        self._mirror_path.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
