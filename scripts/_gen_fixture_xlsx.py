#!/usr/bin/env python3
"""
One-shot generator for fixture register tables (xlsx).

Run once to materialize:
  evals/fixtures/uart16550/spec/uart_regs.xlsx
  evals/fixtures/aes128/spec/aes_regs.xlsx

The xlsx schema is the same one gen-tb's parse_regs.py expects, so these
fixtures double as a parser self-test.

Columns: name | offset | width | access | reset | field_name | field_bits | field_access | field_reset | description
"""
from openpyxl import Workbook
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEADER = [
    "name", "offset", "width", "access", "reset",
    "field_name", "field_bits", "field_access", "field_reset", "description",
]


def write_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "registers"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"wrote {path}")


# ---------- uart16550 ----------
# Register set per OpenCores uart16550. The DUT exposes a 32-bit data /
# 5-bit word-address bus, so we present byte offsets in multiples of 4
# (the APB tb is 32-bit). Some addresses share registers selected by
# DLAB (LCR[7]) — those are encoded as repeated offsets.
_uart_base = [
    ("RBR", 0x00, 8, "RO", 0x00, "data", "7:0", "RO", 0, "Receive buffer (DLAB=0)"),
    ("THR", 0x00, 8, "WO", 0x00, "data", "7:0", "WO", 0, "Transmit holding (DLAB=0)"),
    ("DLL", 0x00, 8, "RW", 0x00, "div_lo", "7:0", "RW", 0, "Divisor latch low (DLAB=1)"),
    ("IER", 0x01, 8, "RW", 0x00, "erbfi", "0", "RW", 0, "Enable received data avail interrupt"),
    ("IER", 0x01, 8, "RW", 0x00, "etbei", "1", "RW", 0, "Enable transmitter holding empty interrupt"),
    ("IER", 0x01, 8, "RW", 0x00, "elsi",  "2", "RW", 0, "Enable receiver line status interrupt"),
    ("IER", 0x01, 8, "RW", 0x00, "edssi", "3", "RW", 0, "Enable modem status interrupt"),
    ("DLM", 0x01, 8, "RW", 0x00, "div_hi", "7:0", "RW", 0, "Divisor latch high (DLAB=1)"),
    ("IIR", 0x02, 8, "RO", 0x01, "pend", "0", "RO", 1, "0 = interrupt pending"),
    ("IIR", 0x02, 8, "RO", 0x01, "id",   "3:1", "RO", 0, "Interrupt ID"),
    ("FCR", 0x02, 8, "WO", 0x00, "fifo_en", "0", "WO", 0, "FIFO enable"),
    ("FCR", 0x02, 8, "WO", 0x00, "rx_clr", "1", "WO", 0, "Clear RX FIFO"),
    ("FCR", 0x02, 8, "WO", 0x00, "tx_clr", "2", "WO", 0, "Clear TX FIFO"),
    ("FCR", 0x02, 8, "WO", 0x00, "trig",   "7:6", "WO", 0, "RX trigger level"),
    ("LCR", 0x03, 8, "RW", 0x00, "wls",  "1:0", "RW", 0, "Word length select"),
    ("LCR", 0x03, 8, "RW", 0x00, "stb",  "2", "RW", 0, "Stop bits"),
    ("LCR", 0x03, 8, "RW", 0x00, "pen",  "3", "RW", 0, "Parity enable"),
    ("LCR", 0x03, 8, "RW", 0x00, "eps",  "4", "RW", 0, "Even parity select"),
    ("LCR", 0x03, 8, "RW", 0x00, "bc",   "6", "RW", 0, "Break control"),
    ("LCR", 0x03, 8, "RW", 0x00, "dlab", "7", "RW", 0, "Divisor latch access bit"),
    ("MCR", 0x04, 8, "RW", 0x00, "dtr",  "0", "RW", 0, "Data terminal ready"),
    ("MCR", 0x04, 8, "RW", 0x00, "rts",  "1", "RW", 0, "Request to send"),
    ("MCR", 0x04, 8, "RW", 0x00, "loop", "4", "RW", 0, "Loopback"),
    ("LSR", 0x05, 8, "RO", 0x60, "dr",   "0", "RO", 0, "Data ready"),
    ("LSR", 0x05, 8, "RO", 0x60, "oe",   "1", "RO", 0, "Overrun error"),
    ("LSR", 0x05, 8, "RO", 0x60, "pe",   "2", "RO", 0, "Parity error"),
    ("LSR", 0x05, 8, "RO", 0x60, "fe",   "3", "RO", 0, "Framing error"),
    ("LSR", 0x05, 8, "RO", 0x60, "bi",   "4", "RO", 0, "Break interrupt"),
    ("LSR", 0x05, 8, "RO", 0x60, "thre", "5", "RO", 1, "Transmitter holding empty"),
    ("LSR", 0x05, 8, "RO", 0x60, "temt", "6", "RO", 1, "Transmitter empty"),
    ("MSR", 0x06, 8, "RO", 0x00, "cts",  "4", "RO", 0, "Clear to send"),
    ("MSR", 0x06, 8, "RO", 0x00, "dsr",  "5", "RO", 0, "Data set ready"),
    ("SCR", 0x07, 8, "RW", 0x00, "data", "7:0", "RW", 0, "Scratch register"),
]
uart_rows = [(n, off * 4, w, a, r, fn, fb, fa, fr, d)
             for (n, off, w, a, r, fn, fb, fa, fr, d) in _uart_base]

# ---------- aes128 ----------
# Mirrors aes.v from secworks/aes (byte-offset = word-index * 4).
aes_rows = [
    ("NAME0",   0x00, 32, "RO", 0x61657320, "name", "31:0", "RO", 0x61657320, "Core name ASCII 'aes '"),
    ("NAME1",   0x04, 32, "RO", 0x20202020, "name", "31:0", "RO", 0x20202020, "Core name ASCII '    '"),
    ("VERSION", 0x08, 32, "RO", 0x302E3630, "ver",  "31:0", "RO", 0x302E3630, "Core version ASCII"),
    ("CTRL",    0x20, 32, "RW", 0x00000000, "init",   "0", "WO", 0, "1 = start key expansion"),
    ("CTRL",    0x20, 32, "RW", 0x00000000, "next",   "1", "WO", 0, "1 = process next block"),
    ("CTRL",    0x20, 32, "RW", 0x00000000, "encdec", "2", "RW", 0, "1 = encrypt, 0 = decrypt"),
    ("CTRL",    0x20, 32, "RW", 0x00000000, "keylen", "3", "RW", 0, "0 = 128-bit key, 1 = 256-bit"),
    ("STATUS",  0x24, 32, "RO", 0x00000001, "ready",  "0", "RO", 1, "1 = core idle / key expansion done"),
    ("STATUS",  0x24, 32, "RO", 0x00000001, "valid",  "1", "RO", 0, "1 = result valid"),
    ("CONFIG",  0x28, 32, "RW", 0x00000000, "cfg",  "31:0", "RW", 0, "Reserved / future use"),
    ("KEY0",    0x40, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 0 (MSB)"),
    ("KEY1",    0x44, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 1"),
    ("KEY2",    0x48, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 2"),
    ("KEY3",    0x4C, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 3 (LSB for 128-bit)"),
    ("KEY4",    0x50, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 4 (256-bit only)"),
    ("KEY5",    0x54, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 5 (256-bit only)"),
    ("KEY6",    0x58, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 6 (256-bit only)"),
    ("KEY7",    0x5C, 32, "WO", 0x00000000, "key",  "31:0", "WO", 0, "Key word 7 (LSB for 256-bit)"),
    ("BLOCK0",  0x80, 32, "WO", 0x00000000, "data", "31:0", "WO", 0, "Plaintext word 0 (MSB)"),
    ("BLOCK1",  0x84, 32, "WO", 0x00000000, "data", "31:0", "WO", 0, "Plaintext word 1"),
    ("BLOCK2",  0x88, 32, "WO", 0x00000000, "data", "31:0", "WO", 0, "Plaintext word 2"),
    ("BLOCK3",  0x8C, 32, "WO", 0x00000000, "data", "31:0", "WO", 0, "Plaintext word 3 (LSB)"),
    ("RESULT0", 0xC0, 32, "RO", 0x00000000, "data", "31:0", "RO", 0, "Ciphertext word 0 (MSB)"),
    ("RESULT1", 0xC4, 32, "RO", 0x00000000, "data", "31:0", "RO", 0, "Ciphertext word 1"),
    ("RESULT2", 0xC8, 32, "RO", 0x00000000, "data", "31:0", "RO", 0, "Ciphertext word 2"),
    ("RESULT3", 0xCC, 32, "RO", 0x00000000, "data", "31:0", "RO", 0, "Ciphertext word 3 (LSB)"),
]


def main():
    write_xlsx(ROOT / "evals/fixtures/uart16550/spec/uart_regs.xlsx", uart_rows)
    write_xlsx(ROOT / "evals/fixtures/aes128/spec/aes_regs.xlsx", aes_rows)


if __name__ == "__main__":
    main()
