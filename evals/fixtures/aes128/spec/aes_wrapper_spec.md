# AES-128 APB Wrapper — Design Specification

## Overview

This IP is an APB3 slave that exposes the secworks/aes core as a memory-mapped
peripheral. The core supports AES-128 and AES-256 encryption/decryption in ECB
mode. For the gen-tb evaluation fixture, only AES-128 ECB is exercised.

## Interfaces

| Signal    | Dir | Width | Description                          |
|-----------|-----|-------|--------------------------------------|
| `pclk`    | in  | 1     | APB clock                            |
| `presetn` | in  | 1     | Active-low synchronous reset         |
| `psel`    | in  | 1     | APB select                           |
| `penable` | in  | 1     | APB enable (access phase)            |
| `pwrite`  | in  | 1     | 1 = write, 0 = read                  |
| `paddr`   | in  | 12    | Byte address; bits [1:0] must be 0   |
| `pwdata`  | in  | 32    | Write data                           |
| `prdata`  | out | 32    | Read data                            |
| `pready`  | out | 1     | Always 1 (zero wait-state)           |
| `pslverr` | out | 1     | Always 0 (no error reporting)        |

## Functional behavior

### Operation sequence (encryption, AES-128 ECB)

1. Program `KEY0..KEY3` with the 128-bit key (KEY0 = MSB word).
2. Program `CTRL.KEYLEN = 0` (128-bit), `CTRL.ENCDEC = 1` (encrypt).
3. Pulse `CTRL.INIT = 1` to perform key expansion. Wait for `STATUS.READY = 1`.
4. Program `BLOCK0..BLOCK3` with the 128-bit plaintext (BLOCK0 = MSB word).
5. Pulse `CTRL.NEXT = 1` to start the block cipher. Wait for `STATUS.VALID = 1`.
6. Read `RESULT0..RESULT3` for the 128-bit ciphertext (RESULT0 = MSB word).

Decryption follows the same sequence with `CTRL.ENCDEC = 0`.

### Reset behavior

`presetn = 0` clears all internal state. `STATUS.READY` is 1 after reset.

### Reference model

The C reference model `ref_model/aes_ref.c` implements AES-128 ECB per
NIST FIPS-197. The scoreboard MUST compare DUT ciphertext output against
the reference model output bit-for-bit.

## Register summary

See `aes_regs.xlsx` for the authoritative register table. The wrapper maps
each APB word address `paddr[9:2]` directly to the core's 8-bit register
index.

## Out of scope for this fixture

- AES-256 (KEYLEN=1) — only 128-bit tested
- CBC, CTR, GCM modes — only ECB
- Interrupt output — none
