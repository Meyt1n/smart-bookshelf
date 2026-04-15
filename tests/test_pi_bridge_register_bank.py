from __future__ import annotations

import os
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pi_bridge.i2c_backends import RegisterPointerProtocolMixin
from pi_bridge.register_bank import MemoryRegisterBank, RegisterBusyError


class DummyProtocol(RegisterPointerProtocolMixin):
    pass


class PiBridgeRegisterBankTests(unittest.TestCase):
    def test_stage_command_updates_registers_in_ordered_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            mirror_path = os.path.join(tempdir, "registers.json")
            bank = MemoryRegisterBank(mirror_path=mirror_path)

            snapshot = bank.stage_command(cmd=1, floor_id=2, cell_id=3, cid=7, title="Test Book")

            self.assertEqual(snapshot["registers"]["reg0"], 1)
            self.assertEqual(snapshot["registers"]["reg1"], 1)
            self.assertEqual(snapshot["registers"]["reg2"], 2)
            self.assertEqual(snapshot["registers"]["reg3"], 3)
            self.assertEqual(snapshot["registers"]["reg4"], 0)
            self.assertEqual(snapshot["last_command"]["cid"], 7)
            self.assertTrue(os.path.exists(mirror_path))

    def test_stage_command_rejects_when_previous_command_is_pending(self):
        bank = MemoryRegisterBank()
        bank.stage_command(cmd=1, floor_id=2, cell_id=3)

        with self.assertRaises(RegisterBusyError):
            bank.stage_command(cmd=2, floor_id=4, cell_id=5)

    def test_reset_clears_all_registers(self):
        bank = MemoryRegisterBank()
        bank.stage_command(cmd=1, floor_id=2, cell_id=3)

        snapshot = bank.reset()

        self.assertEqual(snapshot["registers"], {"reg0": 0, "reg1": 0, "reg2": 0, "reg3": 0, "reg4": 0})

    def test_register_pointer_protocol_supports_mem_read_write_pattern(self):
        bank = MemoryRegisterBank()
        protocol = DummyProtocol(bank)
        bank.stage_command(cmd=1, floor_id=2, cell_id=3)

        protocol.handle_master_write(b"\x00")
        self.assertEqual(protocol.build_tx_payload()[:4], bytes([1, 1, 2, 3]))

        protocol.handle_master_write(b"\x04\x02")
        self.assertEqual(bank.snapshot()["registers"]["reg4"], 2)

        protocol.handle_master_write(b"\x00\x00")
        self.assertEqual(bank.snapshot()["registers"]["reg0"], 0)


if __name__ == "__main__":
    unittest.main()
