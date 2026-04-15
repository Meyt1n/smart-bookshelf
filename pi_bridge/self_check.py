from __future__ import annotations

import json
import os
import platform
import socket
import sys
from pathlib import Path


def detect_smbus():
    info = {
        "python_module": False,
        "bus_openable": False,
        "device_path": "",
        "error": "",
    }
    bus_number = int(os.getenv("PI_BRIDGE_I2C_BUS", "1").strip() or "1")
    info["device_path"] = f"/dev/i2c-{bus_number}"

    import_error = None
    for module_name in ("smbus2", "smbus"):
        try:
            module = __import__(module_name)
            info["python_module"] = True
            bus = module.SMBus(bus_number)
            info["bus_openable"] = True
            close = getattr(bus, "close", None)
            if close is not None:
                close()
            return info
        except Exception as exc:
            import_error = exc

    if not Path(info["device_path"]).exists():
        info["error"] = f"{info['device_path']} does not exist"
    elif import_error is not None:
        info["error"] = f"SMBus open failed: {import_error}"
    return info


def detect_i2c_device_path():
    bus_number = int(os.getenv("PI_BRIDGE_I2C_BUS", "1").strip() or "1")
    path = Path(f"/dev/i2c-{bus_number}")
    info = {
        "path": str(path),
        "exists": path.exists(),
    }
    return info


def detect_http_target():
    host = os.getenv("PI_BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("PI_BRIDGE_PORT", "8765").strip() or "8765")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect((host, port))
        return {"host": host, "port": port, "reachable": True}
    except Exception:
        return {"host": host, "port": port, "reachable": False}
    finally:
        try:
            sock.close()
        except Exception:
            pass


def main():
    result = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "bridge_env": {
            "backend": os.getenv("PI_BRIDGE_BACKEND", "smbus_i2c").strip().lower() or "smbus_i2c",
            "slave_addr": os.getenv("PI_BRIDGE_SLAVE_ADDR", "0x30"),
            "i2c_bus": os.getenv("PI_BRIDGE_I2C_BUS", "1"),
            "host": os.getenv("PI_BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1",
            "port": os.getenv("PI_BRIDGE_PORT", "8765").strip() or "8765",
            "mirror_path": os.getenv("PI_BRIDGE_MIRROR_PATH", "./runtime/registers.json").strip() or None,
            "log_path": os.getenv("PI_BRIDGE_LOG_PATH", "./runtime/bridge.log").strip() or None,
        },
        "paths": {
            "cwd": str(Path.cwd()),
            "script_dir": str(Path(__file__).resolve().parent),
        },
        "i2c_device": detect_i2c_device_path(),
        "smbus": detect_smbus(),
        "http_probe": detect_http_target(),
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
