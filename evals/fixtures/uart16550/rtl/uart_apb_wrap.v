//======================================================================
// uart_apb_wrap.v
//
// APB3 slave wrapper around the OpenCores uart16550 Wishbone core.
// Presents standard byte-aligned register access (one reg per 4-byte
// PADDR offset) by inverting uart_wb.v's 32-bit-mode byte-packing
// scheme.
//
// 16550 reg index N (0..7) is reached via PADDR = N * 4.
// Internally:
//   wb_adr_is[4:2] <= reg_idx[2:0]
//   wb_sel        <= 4'b1000 >> reg_idx[1:0]
//   prdata byte selected from wb_dat_o[(3-reg_idx[1:0])*8 +: 8]
//   pwdata byte placed into wb_dat_is[(3-reg_idx[1:0])*8 +: 8]
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

    // ---- reg_idx = PADDR[4:2] (8 regs at PADDR=0,4,8,...,1C) ----
    wire [2:0] reg_idx     = paddr[4:2];
    wire [1:0] lane        = reg_idx[1:0];

    // wb_adr_int[4:2] = wb_adr_is[4:2]; we want wb_adr_int to be reg_idx
    // (0..7) with the low 2 bits supplied by wb_sel. So reg_idx[2] goes
    // into wb_adr_is[2], and bits [4:3] are zero (since reg_idx ≤ 7).
    wire [4:0] wb_adr      = {2'b00, reg_idx};

    // sel encoding (per uart_wb.v 32-bit-mode mapping)
    reg  [3:0] wb_sel;
    always @* begin
        case (lane)
            2'b00:   wb_sel = 4'b1000;
            2'b01:   wb_sel = 4'b0100;
            2'b10:   wb_sel = 4'b0010;
            default: wb_sel = 4'b0001;
        endcase
    end

    // Byte-lane shift for the WRITE path. wb_dat8_i = wb_dat_is[(3-lane)*8 +: 8].
    // So pwdata[7:0] must be placed at that bit position.
    reg [31:0] wb_dat_i_drv;
    always @* begin
        case (lane)
            2'b00:   wb_dat_i_drv = {pwdata[7:0],        24'b0};   // bits [31:24]
            2'b01:   wb_dat_i_drv = { 8'b0, pwdata[7:0], 16'b0};   // bits [23:16]
            2'b10:   wb_dat_i_drv = {16'b0, pwdata[7:0],  8'b0};   // bits [15:8]
            default: wb_dat_i_drv = {24'b0, pwdata[7:0]};          // bits [7:0]
        endcase
    end

    // APB → WB cycle gating
    wire        wb_cyc  = psel & penable;
    wire        wb_stb  = wb_cyc;
    wire        wb_we   = wb_cyc & pwrite;
    wire [31:0] wb_dat_o_core;
    wire        wb_ack;

    // Byte-lane shift for the READ path. wb_dat_o has the 8-bit reg at
    // the same bit position dictated by sel.  Strip back down to byte 0.
    reg [7:0] read_byte;
    always @* begin
        case (lane)
            2'b00:   read_byte = wb_dat_o_core[31:24];
            2'b01:   read_byte = wb_dat_o_core[23:16];
            2'b10:   read_byte = wb_dat_o_core[15:8];
            default: read_byte = wb_dat_o_core[7:0];
        endcase
    end

    assign prdata  = {24'b0, read_byte};
    assign pready  = wb_ack;
    assign pslverr = 1'b0;

    uart_top u_uart (
        .wb_clk_i   (pclk),
        .wb_rst_i   (~presetn),
        .wb_adr_i   (wb_adr),
        .wb_dat_i   (wb_dat_i_drv),
        .wb_dat_o   (wb_dat_o_core),
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
