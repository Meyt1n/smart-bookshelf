from __future__ import annotations

from dataclasses import dataclass
import time


ACK_PENDING = 0xFF
DEFAULT_ACK_TIMEOUT_MS = 8000
DEFAULT_POLL_INTERVAL_MS = 50

ACK_MESSAGES = {
    0x00: "task accepted",
    0x01: "stm32 busy",
    0x02: "invalid floor or cell parameter",
    0x03: "stm32 reported system fault",
    0x04: "unknown command",
    0x05: "stm32 i2c handler error",
    ACK_PENDING: "ack pending",
}


def _coerce_rx_bytes(rx_data, count: int) -> bytes:
    if rx_data is None or count <= 0:
        return b""
    if isinstance(rx_data, bytes):
        return rx_data[:count]
    if isinstance(rx_data, bytearray):
        return bytes(rx_data[:count])
    if isinstance(rx_data, str):
        return rx_data.encode("latin1", errors="ignore")[:count]
    return bytes(rx_data)[:count]


@dataclass
class BackendInfo:
    kind: str
    ready: bool
    detail: str = ""

    def as_dict(self):
        return {
            "kind": self.kind,
            "ready": bool(self.ready),
            "detail": self.detail,
        }


class MotionDispatchError(RuntimeError):
    pass


class AckTimeoutError(MotionDispatchError):
    pass


class MotionRejectedError(MotionDispatchError):
    def __init__(self, ack: int, detail: str = ""):
        self.ack = int(ack) & 0xFF
        self.detail = detail or ACK_MESSAGES.get(self.ack, "command rejected")
        super().__init__(f"{self.detail} (ack=0x{self.ack:02X})")


class NullI2CBackend:
    def __init__(self, register_bank):
        self._register_bank = register_bank

    def start(self):
        return None

    def stop(self):
        return None

    def read_snapshot(self):
        return self._register_bank.snapshot()

    def reset(self):
        return self._register_bank.reset()

    def dispatch_command(
        self,
        *,
        cmd: int,
        floor_id: int,
        cell_id: int,
        cid=None,
        title="",
        ack_timeout_ms: int = DEFAULT_ACK_TIMEOUT_MS,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ):
        self._register_bank.stage_command(cmd=cmd, floor_id=floor_id, cell_id=cell_id, cid=cid, title=title)
        self._register_bank.write_registers(4, [0])
        return self._register_bank.write_registers(0, [0])

    def info(self) -> BackendInfo:
        return BackendInfo(kind="memory", ready=True, detail="in-memory register bank only")


class RegisterPointerProtocolMixin:
    def __init__(self, register_bank):
        self._register_bank = register_bank
        self._pointer = 0

    def handle_master_write(self, rx_bytes: bytes):
        if not rx_bytes:
            return self._register_bank.snapshot()

        start_register = int(rx_bytes[0])
        if len(rx_bytes) == 1:
            self._pointer = max(0, start_register)
            return self._register_bank.snapshot()

        snapshot = self._register_bank.write_registers(start_register, rx_bytes[1:])
        self._pointer = max(0, start_register + len(rx_bytes) - 1)
        return snapshot

    def build_tx_payload(self, max_count: int = 32) -> bytes:
        payload = self._register_bank.read_window(self._pointer)
        if not payload:
            payload = b"\x00"
        return payload[:max_count]


class PigpioI2CBackend(RegisterPointerProtocolMixin):
    def __init__(self, register_bank, *, slave_address: int):
        super().__init__(register_bank)
        self._slave_address = int(slave_address) & 0x7F
        self._pigpio = None
        self._pi = None
        self._callback = None
        self._last_error = ""

    def start(self):
        try:
            import pigpio  # type: ignore
        except Exception as exc:
            raise RuntimeError("pigpio is not installed in this Python environment") from exc

        self._pigpio = pigpio
        self._pi = pigpio.pi()
        if not getattr(self._pi, "connected", False):
            raise RuntimeError("pigpio daemon is not reachable; start pigpiod first")

        self._callback = self._pi.event_callback(pigpio.EVENT_BSC, self._on_bsc_event)
        self._push_tx_payload()

    def stop(self):
        if self._pi is None:
            return

        try:
            if self._callback is not None:
                self._callback.cancel()
        except Exception:
            pass

        try:
            self._pi.bsc_i2c(0)
        except Exception:
            pass

        try:
            self._pi.stop()
        except Exception:
            pass

        self._callback = None
        self._pi = None

    def info(self) -> BackendInfo:
        detail = self._last_error or f"slave address=0x{self._slave_address:02X}"
        return BackendInfo(kind="pigpio_i2c", ready=self._pi is not None, detail=detail)

    def _push_tx_payload(self):
        if self._pi is None:
            return
        tx_bytes = self.build_tx_payload()
        self._pi.bsc_i2c(self._slave_address, tx_bytes)

    def _on_bsc_event(self, _event, _tick):
        if self._pi is None:
            return

        try:
            _status, count, rx_data = self._pi.bsc_i2c(self._slave_address, self.build_tx_payload())
            master_bytes = _coerce_rx_bytes(rx_data, count)
            if master_bytes:
                self.handle_master_write(master_bytes)
            self._push_tx_payload()
        except Exception as exc:
            self._last_error = str(exc)


class SmbusI2CMasterBackend:
    def __init__(self, register_bank, *, slave_address: int, bus_number: int):
        self._register_bank = register_bank
        self._slave_address = int(slave_address) & 0x7F
        self._bus_number = int(bus_number)
        self._bus = None
        self._last_error = ""

    def start(self):
        if self._bus is not None:
            return

        import_error = None
        for module_name in ("smbus2", "smbus"):
            try:
                module = __import__(module_name)
                self._bus = module.SMBus(self._bus_number)
                self._last_error = ""
                return
            except Exception as exc:
                import_error = exc

        raise RuntimeError(
            f"failed to open I2C bus {self._bus_number}; install smbus2 and enable /dev/i2c-{self._bus_number}"
        ) from import_error

    def stop(self):
        if self._bus is None:
            return

        try:
            close = getattr(self._bus, "close", None)
            if close is not None:
                close()
        finally:
            self._bus = None

    def info(self) -> BackendInfo:
        detail = self._last_error or f"bus={self._bus_number} addr=0x{self._slave_address:02X}"
        return BackendInfo(kind="smbus_i2c", ready=self._bus is not None, detail=detail)

    def read_snapshot(self):
        bus = self._require_bus()
        try:
            registers = {
                f"reg{index}": int(bus.read_byte_data(self._slave_address, index)) & 0xFF
                for index in range(5)
            }
            return self._register_bank.load_snapshot(registers)
        except Exception as exc:
            self._last_error = str(exc)
            raise RuntimeError(f"failed to read STM32 registers over I2C: {exc}") from exc

    def reset(self):
        bus = self._require_bus()
        try:
            for index in range(5):
                bus.write_byte_data(self._slave_address, index, 0)
            return self._register_bank.load_snapshot([0, 0, 0, 0, 0], last_command=None)
        except Exception as exc:
            self._last_error = str(exc)
            raise RuntimeError(f"failed to reset STM32 register bank over I2C: {exc}") from exc

    def dispatch_command(
        self,
        *,
        cmd: int,
        floor_id: int,
        cell_id: int,
        cid=None,
        title="",
        ack_timeout_ms: int = DEFAULT_ACK_TIMEOUT_MS,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ):
        bus = self._require_bus()
        last_command = {
            "cmd": int(cmd),
            "floor_id": int(floor_id),
            "cell_id": int(cell_id),
            "cid": cid,
            "title": title or "",
            "staged_at": time.time(),
        }

        live_snapshot = self.read_snapshot()
        live_registers = live_snapshot.get("registers") or {}
        if int(live_registers.get("reg0", 0)) != 0:
            raise MotionRejectedError(int(live_registers.get("reg4", ACK_PENDING)), "stm32 still has a pending command")

        try:
            bus.write_byte_data(self._slave_address, 4, ACK_PENDING)
            bus.write_byte_data(self._slave_address, 1, int(cmd) & 0xFF)
            bus.write_byte_data(self._slave_address, 2, int(floor_id) & 0xFF)
            bus.write_byte_data(self._slave_address, 3, int(cell_id) & 0xFF)
            bus.write_byte_data(self._slave_address, 0, 1)
            self._register_bank.set_last_command(
                cmd=cmd,
                floor_id=floor_id,
                cell_id=cell_id,
                cid=cid,
                title=title,
            )
            self._register_bank.load_snapshot(
                {
                    "reg0": 1,
                    "reg1": int(cmd) & 0xFF,
                    "reg2": int(floor_id) & 0xFF,
                    "reg3": int(cell_id) & 0xFF,
                    "reg4": ACK_PENDING,
                },
                last_command=last_command,
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise RuntimeError(f"failed to write STM32 command over I2C: {exc}") from exc

        deadline = time.time() + max(0.1, int(ack_timeout_ms) / 1000.0)
        sleep_s = max(0.01, int(poll_interval_ms) / 1000.0)

        while time.time() < deadline:
            snapshot = self.read_snapshot()
            registers = snapshot.get("registers") or {}
            ack = int(registers.get("reg4", ACK_PENDING)) & 0xFF
            if int(registers.get("reg0", 0)) == 0 and ack != ACK_PENDING:
                if ack == 0:
                    return snapshot
                raise MotionRejectedError(ack, ACK_MESSAGES.get(ack, "command rejected by stm32"))
            time.sleep(sleep_s)

        raise AckTimeoutError(
            f"timed out waiting for STM32 ACK after {int(ack_timeout_ms)} ms on /dev/i2c-{self._bus_number}"
        )

    def _require_bus(self):
        if self._bus is None:
            raise RuntimeError("I2C backend is not started")
        return self._bus
