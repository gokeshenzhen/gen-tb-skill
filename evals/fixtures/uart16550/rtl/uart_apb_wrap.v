//======================================================================
// uart_apb_wrap.v
//
// APB3 slave wrapper around the OpenCores uart16550 Wishbone core.
// Generates a Wishbone classic single-transfer cycle per APB access.
//
// Address mapping:
//   PADDR[6:2] -> wb_adr_i[4:0]   (5-bit word index into 16550 regs)
//   PADDR[1:0] must be 2'b00.
//
// The uart_top responds combinationally (ack typically same cycle in
// the OpenCores design when not in a fifo-blocking case), so this
// wrapper inserts at most one APB wait state by deasserting pready
// until wb_ack arrives.
//
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 gen-tb contributors
//======================================================================

`default_nettype none

module uart_apb_wrap (
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
    output wire        pslverr,

    // UART pads
    output wire        irq,
    output wire        stx_pad_o,
    input  wire        srx_pad_i,
    output wire        rts_pad_o,
    input  wire        cts_pad_i,
    output wire        dtr_pad_o,
    input  wire        dsr_pad_i,
    input  wire        ri_pad_i,
    input  wire        dcd_pad_i
);

    wire        wb_stb;
    wire        wb_cyc;
    wire        wb_we;
    wire [4:0]  wb_adr;
    wire [31:0] wb_dat_o;   // tb -> core
    wire [31:0] wb_dat_i;   // core -> tb
    wire        wb_ack;
    // TODO(v1.1): this wrapper has a known issue interfacing with
    // uart_wb's 32-bit mode byte-packing scheme (wb_adr_int[1:0] is
    // overwritten from wb_sel, packing 4 8-bit regs per 32-bit word).
    // The current wb_sel=4'b0001 only reaches the byte-3 lane (LCR
    // for paddr=0). For correct standard 16550 access, either rebuild
    // the fixture with DATA_BUS_WIDTH_8 mode or write a byte-lane
    // muxing wrapper that decodes PADDR[4:0] into wb_adr+wb_sel
    // pairs. See docs/aes128_dryrun_v1_gaps.md follow-up.
    wire [3:0]  wb_sel = 4'b0001;

    // Drive WB cycle during APB access phase (psel & penable).
    assign wb_cyc = psel & penable;
    assign wb_stb = wb_cyc;
    assign wb_we  = wb_cyc & pwrite;
    assign wb_adr = paddr[6:2];
    assign wb_dat_o = pwdata;

    assign prdata  = wb_dat_i;
    assign pready  = wb_ack;
    assign pslverr = 1'b0;

    uart_top u_uart (
        .wb_clk_i   (pclk),
        .wb_rst_i   (~presetn),
        .wb_adr_i   (wb_adr),
        .wb_dat_i   (wb_dat_o),
        .wb_dat_o   (wb_dat_i),
        .wb_we_i    (wb_we),
        .wb_stb_i   (wb_stb),
        .wb_cyc_i   (wb_cyc),
        .wb_ack_o   (wb_ack),
        .wb_sel_i   (wb_sel),
        .int_o      (irq),
        .stx_pad_o  (stx_pad_o),
        .srx_pad_i  (srx_pad_i),
        .rts_pad_o  (rts_pad_o),
        .cts_pad_i  (cts_pad_i),
        .dtr_pad_o  (dtr_pad_o),
        .dsr_pad_i  (dsr_pad_i),
        .ri_pad_i   (ri_pad_i),
        .dcd_pad_i  (dcd_pad_i)
    );

endmodule

`default_nettype wire
