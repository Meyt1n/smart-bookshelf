from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from pathlib import Path


def render_service(
    *,
    bridge_dir: Path,
    python_bin: str,
    user: str,
    backend: str,
    host: str,
    port: int,
    slave_addr: str,
    i2c_bus: int,
    log_path: Path,
):
    bridge_dir_text = bridge_dir.as_posix()
    log_path_text = log_path.as_posix()
    exec_path_text = (bridge_dir / "bridge_server.py").as_posix()
    return f"""[Unit]
Description=Smart Bookshelf Raspberry Pi Bridge
After=network.target

[Service]
Type=simple
WorkingDirectory={bridge_dir_text}
Environment=PI_BRIDGE_HOST={host}
Environment=PI_BRIDGE_PORT={port}
Environment=PI_BRIDGE_BACKEND={backend}
Environment=PI_BRIDGE_SLAVE_ADDR={slave_addr}
Environment=PI_BRIDGE_I2C_BUS={i2c_bus}
Environment=PI_BRIDGE_LOG_PATH={log_path_text}
ExecStart={python_bin} {exec_path_text}
Restart=always
RestartSec=2
User={user}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def run_systemctl(*args):
    subprocess.run(["systemctl", *args], check=True)


def main():
    bridge_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Render or install the smart bookshelf pi bridge systemd unit.")
    parser.add_argument("--service-name", default="smart-bookshelf-pi-bridge")
    parser.add_argument("--user", default=getpass.getuser())
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--backend", default="smbus_i2c")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--slave-addr", default="0x30")
    parser.add_argument("--i2c-bus", type=int, default=1)
    parser.add_argument("--output")
    parser.add_argument("--write", action="store_true", help="Write the rendered unit file to disk.")
    parser.add_argument("--enable", action="store_true", help="Run systemctl enable after writing.")
    parser.add_argument("--start", action="store_true", help="Run systemctl start after writing.")
    args = parser.parse_args()

    output = Path(args.output) if args.output else Path("/etc/systemd/system") / f"{args.service_name}.service"
    log_path = bridge_dir / "runtime" / "bridge.log"
    content = render_service(
        bridge_dir=bridge_dir,
        python_bin=args.python_bin,
        user=args.user,
        backend=args.backend,
        host=args.host,
        port=args.port,
        slave_addr=args.slave_addr,
        i2c_bus=args.i2c_bus,
        log_path=log_path,
    )

    if not args.write:
        print(content)
        print("\nUse --write to save it, or redirect stdout to a file.")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote systemd unit to: {output}")

    if args.enable or args.start:
        run_systemctl("daemon-reload")
    if args.enable:
        run_systemctl("enable", args.service_name)
        print(f"Enabled service: {args.service_name}")
    if args.start:
        run_systemctl("start", args.service_name)
        print(f"Started service: {args.service_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
