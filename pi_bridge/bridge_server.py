from __future__ import annotations

import atexit
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from register_bank import MemoryRegisterBank, RegisterBusyError
    from i2c_backends import (
        AckTimeoutError,
        MotionRejectedError,
        NullI2CBackend,
        PigpioI2CBackend,
        SmbusI2CMasterBackend,
    )
except ImportError:
    from pi_bridge.register_bank import MemoryRegisterBank, RegisterBusyError
    from pi_bridge.i2c_backends import (
        AckTimeoutError,
        MotionRejectedError,
        NullI2CBackend,
        PigpioI2CBackend,
        SmbusI2CMasterBackend,
    )


HOST = os.getenv("PI_BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("PI_BRIDGE_PORT", "8765").strip() or "8765")
ALLOW_ORIGIN = os.getenv("PI_BRIDGE_ALLOW_ORIGIN", "*").strip() or "*"
MIRROR_PATH = os.getenv("PI_BRIDGE_MIRROR_PATH", "./runtime/registers.json").strip() or None
BACKEND_KIND = os.getenv("PI_BRIDGE_BACKEND", "smbus_i2c").strip().lower() or "smbus_i2c"
SLAVE_ADDRESS = int(os.getenv("PI_BRIDGE_SLAVE_ADDR", "0x30"), 0) & 0x7F
I2C_BUS = int(os.getenv("PI_BRIDGE_I2C_BUS", "1").strip() or "1")
ACK_TIMEOUT_MS = int(os.getenv("PI_BRIDGE_ACK_TIMEOUT_MS", "8000").strip() or "8000")
POLL_INTERVAL_MS = int(os.getenv("PI_BRIDGE_POLL_INTERVAL_MS", "50").strip() or "50")
LOG_PATH = os.getenv("PI_BRIDGE_LOG_PATH", "./runtime/bridge.log").strip() or None

BANK = MemoryRegisterBank(mirror_path=MIRROR_PATH)


def build_backend():
    if BACKEND_KIND == "memory":
        return NullI2CBackend(BANK)
    if BACKEND_KIND == "smbus_i2c":
        return SmbusI2CMasterBackend(BANK, slave_address=SLAVE_ADDRESS, bus_number=I2C_BUS)
    if BACKEND_KIND == "pigpio_i2c":
        return PigpioI2CBackend(BANK, slave_address=SLAVE_ADDRESS)
    raise RuntimeError(f"unsupported PI_BRIDGE_BACKEND: {BACKEND_KIND}")


I2C_BACKEND = build_backend()
atexit.register(I2C_BACKEND.stop)


def build_logger():
    logger = logging.getLogger("pi_bridge")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if LOG_PATH:
        log_path = Path(LOG_PATH).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


LOGGER = build_logger()


def diagnostic_payload():
    register_snapshot = BANK.snapshot()
    try:
        register_snapshot = I2C_BACKEND.read_snapshot()
    except Exception as exc:
        LOGGER.warning("failed to refresh register snapshot: %s", exc)

    return {
        "register_bank": register_snapshot,
        "backend": I2C_BACKEND.info().as_dict(),
        "server": {
            "host": HOST,
            "port": PORT,
            "allow_origin": ALLOW_ORIGIN,
            "mirror_path": MIRROR_PATH,
            "log_path": LOG_PATH,
            "backend_kind": BACKEND_KIND,
            "i2c_bus": I2C_BUS,
            "slave_address_7bit": f"0x{SLAVE_ADDRESS:02X}",
            "ack_timeout_ms": ACK_TIMEOUT_MS,
            "poll_interval_ms": POLL_INTERVAL_MS,
        },
    }


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.send_header("Vary", "Origin, Access-Control-Request-Private-Network")
    handler.end_headers()
    handler.wfile.write(raw)


def parse_json_body(handler: BaseHTTPRequestHandler):
    content_length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(content_length) if content_length > 0 else b"{}"
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception as exc:
        raise ValueError(f"invalid json body: {exc}") from exc


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "SmartBookshelfPiBridge/1.0"

    def log_message(self, format, *args):
        LOGGER.info("[http] " + (format % args))

    def do_OPTIONS(self):
        json_response(self, HTTPStatus.NO_CONTENT, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "pi bridge ready",
                    "data": diagnostic_payload(),
                },
            )
            return

        if path == "/registers":
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "register snapshot",
                    "data": diagnostic_payload(),
                },
            )
            return

        if path == "/diagnostics":
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "diagnostics snapshot",
                    "data": diagnostic_payload(),
                },
            )
            return

        json_response(
            self,
            HTTPStatus.NOT_FOUND,
            {"ok": False, "message": f"unknown path: {path}"},
        )

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/dispatch":
            try:
                body = parse_json_body(self)
                cmd = int(body.get("cmd"))
                floor_id = int(body.get("floor_id"))
                cell_id = int(body.get("cell_id"))
                snapshot = I2C_BACKEND.dispatch_command(
                    cmd=cmd,
                    floor_id=floor_id,
                    cell_id=cell_id,
                    cid=body.get("cid"),
                    title=body.get("title", ""),
                    ack_timeout_ms=ACK_TIMEOUT_MS,
                    poll_interval_ms=POLL_INTERVAL_MS,
                )
                LOGGER.info(
                    "dispatch accepted cmd=%s floor=%s cell=%s cid=%s title=%r",
                    cmd,
                    floor_id,
                    cell_id,
                    body.get("cid"),
                    body.get("title", ""),
                )
            except RegisterBusyError as exc:
                LOGGER.warning("dispatch rejected: %s", exc)
                json_response(self, HTTPStatus.CONFLICT, {"ok": False, "message": str(exc)})
                return
            except MotionRejectedError as exc:
                LOGGER.warning("dispatch rejected by stm32: %s", exc)
                json_response(
                    self,
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "message": str(exc),
                        "ack": exc.ack,
                        "data": diagnostic_payload(),
                    },
                )
                return
            except AckTimeoutError as exc:
                LOGGER.warning("dispatch timed out waiting for stm32 ack: %s", exc)
                json_response(
                    self,
                    HTTPStatus.GATEWAY_TIMEOUT,
                    {
                        "ok": False,
                        "message": str(exc),
                        "data": diagnostic_payload(),
                    },
                )
                return
            except Exception as exc:
                LOGGER.exception("dispatch failed")
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
                return

            json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": "command accepted by STM32",
                    "data": snapshot,
                },
            )
            return

        if path == "/reset":
            try:
                snapshot = I2C_BACKEND.reset()
                LOGGER.info("register bank reset via http")
                json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "message": "register bank reset",
                        "data": snapshot,
                    },
                )
            except Exception as exc:
                LOGGER.exception("register reset failed")
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
            return

        json_response(
            self,
            HTTPStatus.NOT_FOUND,
            {"ok": False, "message": f"unknown path: {path}"},
        )


def main():
    try:
        I2C_BACKEND.start()
        LOGGER.info("listening on http://%s:%s", HOST, PORT)
        if MIRROR_PATH:
            LOGGER.info("register mirror: %s", MIRROR_PATH)
        if LOG_PATH:
            LOGGER.info("bridge log: %s", LOG_PATH)
        LOGGER.info("backend: %s", I2C_BACKEND.info().as_dict())
        LOGGER.info("dispatch rule: write reg4=0xFF, reg1/cmd reg2/floor reg3/cell, then reg0=1")
        httpd = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("stopped by keyboard interrupt")
    except Exception:
        LOGGER.exception("bridge server failed to start")
        raise
    finally:
        I2C_BACKEND.stop()


if __name__ == "__main__":
    main()
