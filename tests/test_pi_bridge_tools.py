from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pi_bridge.install_service import render_service
from pi_bridge.smoke_test import extract_register_block, extract_registers


class PiBridgeToolsTests(unittest.TestCase):
    def test_render_service_contains_expected_runtime_values(self):
        bridge_dir = Path("/home/pi/smart_bookshelf/pi_bridge")
        content = render_service(
            bridge_dir=bridge_dir,
            python_bin="/usr/bin/python3",
            user="pi",
            backend="smbus_i2c",
            host="127.0.0.1",
            port=8765,
            slave_addr="0x30",
            i2c_bus=1,
            log_path=bridge_dir / "runtime" / "bridge.log",
        )

        self.assertIn("WorkingDirectory=/home/pi/smart_bookshelf/pi_bridge", content)
        self.assertIn("ExecStart=/usr/bin/python3 /home/pi/smart_bookshelf/pi_bridge/bridge_server.py", content)
        self.assertIn("Environment=PI_BRIDGE_BACKEND=smbus_i2c", content)
        self.assertIn("Environment=PI_BRIDGE_SLAVE_ADDR=0x30", content)
        self.assertIn("Environment=PI_BRIDGE_I2C_BUS=1", content)

    def test_extract_registers_accepts_diagnostics_shape(self):
        payload = {
            "ok": True,
            "data": {
                "register_bank": {
                    "registers": {
                        "reg0": 1,
                        "reg1": 2,
                        "reg2": 3,
                        "reg3": 4,
                        "reg4": 0,
                    }
                }
            },
        }

        block = extract_register_block(payload)
        registers = extract_registers(payload)

        self.assertIn("registers", block)
        self.assertEqual(registers["reg0"], 1)
        self.assertEqual(registers["reg3"], 4)


if __name__ == "__main__":
    unittest.main()
