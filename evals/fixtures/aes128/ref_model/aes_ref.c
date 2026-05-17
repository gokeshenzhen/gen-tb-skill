/*
 * aes_ref.c
 *
 * DPI-callable reference model for AES-128 ECB.
 * Thin wrapper over tiny-AES-c (kokke/tiny-AES-c, public domain).
 *
 * Expected DPI import on the SystemVerilog side (one block, big-endian
 * word order matching the DUT's KEY0..3 / BLOCK0..3 / RESULT0..3 layout):
 *
 *   import "DPI-C" function void aes128_ecb_encrypt(
 *       input  bit [31:0] key   [0:3],
 *       input  bit [31:0] din   [0:3],
 *       output bit [31:0] dout  [0:3]);
 *
 *   import "DPI-C" function void aes128_ecb_decrypt(
 *       input  bit [31:0] key   [0:3],
 *       input  bit [31:0] din   [0:3],
 *       output bit [31:0] dout  [0:3]);
 *
 * SPDX-License-Identifier: Apache-2.0
 * Copyright 2026 gen-tb contributors
 */

#include <stdint.h>
#include <string.h>
#include "tiny_aes.h"

#define AES128_KEY_BYTES   16
#define AES128_BLOCK_BYTES 16

static void words_be_to_bytes(const uint32_t *words, uint8_t *bytes, int nwords)
{
    for (int i = 0; i < nwords; i++) {
        bytes[i * 4 + 0] = (uint8_t)(words[i] >> 24);
        bytes[i * 4 + 1] = (uint8_t)(words[i] >> 16);
        bytes[i * 4 + 2] = (uint8_t)(words[i] >>  8);
        bytes[i * 4 + 3] = (uint8_t)(words[i]      );
    }
}

static void bytes_be_to_words(const uint8_t *bytes, uint32_t *words, int nwords)
{
    for (int i = 0; i < nwords; i++) {
        words[i] = ((uint32_t)bytes[i * 4 + 0] << 24) |
                   ((uint32_t)bytes[i * 4 + 1] << 16) |
                   ((uint32_t)bytes[i * 4 + 2] <<  8) |
                   ((uint32_t)bytes[i * 4 + 3]      );
    }
}

void aes128_ecb_encrypt(const uint32_t key[4],
                        const uint32_t din[4],
                        uint32_t       dout[4])
{
    uint8_t key_b[AES128_KEY_BYTES];
    uint8_t buf[AES128_BLOCK_BYTES];
    struct AES_ctx ctx;

    words_be_to_bytes(key, key_b, 4);
    words_be_to_bytes(din, buf,   4);

    AES_init_ctx(&ctx, key_b);
    AES_ECB_encrypt(&ctx, buf);

    bytes_be_to_words(buf, dout, 4);
}

void aes128_ecb_decrypt(const uint32_t key[4],
                        const uint32_t din[4],
                        uint32_t       dout[4])
{
    uint8_t key_b[AES128_KEY_BYTES];
    uint8_t buf[AES128_BLOCK_BYTES];
    struct AES_ctx ctx;

    words_be_to_bytes(key, key_b, 4);
    words_be_to_bytes(din, buf,   4);

    AES_init_ctx(&ctx, key_b);
    AES_ECB_decrypt(&ctx, buf);

    bytes_be_to_words(buf, dout, 4);
}
