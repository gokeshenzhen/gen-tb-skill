//======================================================================
// aes_apb_wrap.v
//
// Thin APB3 wrapper around the secworks/aes core.
// Maps APB transactions to the core's native cs/we/address bus.
//
// APB address layout (byte-addressed, word-aligned):
//   PADDR[9:2] -> core address[7:0]
//   PADDR[1:0] must be 2'b00 for legal access.
//
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 gen-tb contributors
//======================================================================

`default_nettype none

module aes_apb_wrap (
    input  wire        pclk,
    input  wire        presetn,

    // APB3 slave
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [11:0] paddr,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr
);

    wire        core_cs;
    wire        core_we;
    wire [7:0]  core_addr;
    wire [31:0] core_wdata;
    wire [31:0] core_rdata;

    assign core_cs    = psel & penable;
    assign core_we    = core_cs & pwrite;
    assign core_addr  = paddr[9:2];
    assign core_wdata = pwdata;

    assign prdata     = core_rdata;
    assign pready     = 1'b1;
    assign pslverr    = 1'b0;

    aes u_aes (
        .clk        (pclk),
        .reset_n    (presetn),
        .cs         (core_cs),
        .we         (core_we),
        .address    (core_addr),
        .write_data (core_wdata),
        .read_data  (core_rdata)
    );

endmodule

`default_nettype wire
