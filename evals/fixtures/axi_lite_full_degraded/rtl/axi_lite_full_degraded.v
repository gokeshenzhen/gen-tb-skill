// AXI4-Lite slave that ALSO exposes full-AXI burst/ID ports, but only
// ever services single-beat transfers. Used to exercise the gen-tb
// AXI4-full degraded-mode path: the Phase 1 detector sets
// axi_full_signature, the interface gets the burst/ID ports + the
// AWLEN/ARLEN==0 assertions, and the TB master ties the burst signals
// to single-beat values.
//
// Register-channel logic is structurally identical to the known-good
// axi_lite_simple_slave fixture; the AWLEN/ARLEN/AWBURST/AWID/WLAST/...
// inputs are accepted but ignored, and the *ID response ports
// (BID/RID) are echoed, RLAST is tied high.
//
//   0x000: ID register, reset 0x000000A5, RO
//   0x004: SCRATCH register, RW
module axi_lite_full_degraded #(
    parameter ADDR_W = 12,
    parameter DATA_W = 32,
    parameter ID_W   = 4
) (
    input  wire              aclk,
    input  wire              aresetn,
    // write address
    input  wire              awvalid,
    output reg               awready,
    input  wire [ADDR_W-1:0] awaddr,
    input  wire [2:0]        awprot,
    input  wire [7:0]        awlen_i,
    input  wire [2:0]        awsize_i,
    input  wire [1:0]        awburst_i,
    input  wire [ID_W-1:0]   awid_i,
    // write data
    input  wire              wvalid,
    output reg               wready,
    input  wire [DATA_W-1:0] wdata,
    input  wire [DATA_W/8-1:0] wstrb,
    input  wire              wlast_i,
    // write response
    output reg               bvalid,
    input  wire              bready,
    output reg  [1:0]        bresp,
    output reg  [ID_W-1:0]   bid_o,
    // read address
    input  wire              arvalid,
    output reg               arready,
    input  wire [ADDR_W-1:0] araddr,
    input  wire [2:0]        arprot,
    input  wire [7:0]        arlen_i,
    input  wire [2:0]        arsize_i,
    input  wire [1:0]        arburst_i,
    input  wire [ID_W-1:0]   arid_i,
    // read data
    output reg               rvalid,
    input  wire              rready,
    output reg  [DATA_W-1:0] rdata,
    output reg  [1:0]        rresp,
    output reg  [ID_W-1:0]   rid_o,
    output reg               rlast_o
);

    localparam [DATA_W-1:0] ID_RESET = 32'h000000A5;

    reg [DATA_W-1:0] scratch;
    reg [ADDR_W-1:0] aw_addr_r;
    reg [ID_W-1:0]   aw_id_r;
    reg              aw_seen;
    reg              w_seen;

    // Write address channel
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            awready   <= 1'b0;
            aw_addr_r <= {ADDR_W{1'b0}};
            aw_id_r   <= {ID_W{1'b0}};
            aw_seen   <= 1'b0;
        end else begin
            if (!aw_seen && awvalid && !awready) begin
                awready   <= 1'b1;
                aw_addr_r <= awaddr;
                aw_id_r   <= awid_i;
                aw_seen   <= 1'b1;
            end else begin
                awready <= 1'b0;
                if (bvalid && bready) aw_seen <= 1'b0;
            end
        end
    end

    // Write data channel
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            wready  <= 1'b0;
            scratch <= {DATA_W{1'b0}};
            w_seen  <= 1'b0;
        end else begin
            if (!w_seen && wvalid && !wready) begin
                wready <= 1'b1;
                w_seen <= 1'b1;
                if (aw_seen ? (aw_addr_r[3:0] == 4'h4) : (awaddr[3:0] == 4'h4))
                    scratch <= wdata;
            end else begin
                wready <= 1'b0;
                if (bvalid && bready) w_seen <= 1'b0;
            end
        end
    end

    // Write response
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            bvalid <= 1'b0;
            bresp  <= 2'b00;
            bid_o  <= {ID_W{1'b0}};
        end else begin
            if (aw_seen && w_seen && !bvalid) begin
                bvalid <= 1'b1;
                bresp  <= 2'b00;
                bid_o  <= aw_id_r;
            end else if (bvalid && bready) begin
                bvalid <= 1'b0;
            end
        end
    end

    // Read channel
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            arready <= 1'b0;
            rvalid  <= 1'b0;
            rdata   <= {DATA_W{1'b0}};
            rresp   <= 2'b00;
            rid_o   <= {ID_W{1'b0}};
            rlast_o <= 1'b1;
        end else begin
            if (arvalid && !arready && !rvalid) begin
                arready <= 1'b1;
                rvalid  <= 1'b1;
                rresp   <= 2'b00;
                rid_o   <= arid_i;
                rlast_o <= 1'b1;
                case (araddr[3:0])
                    4'h0:    rdata <= ID_RESET;
                    4'h4:    rdata <= scratch;
                    default: rdata <= {DATA_W{1'b0}};
                endcase
            end else begin
                arready <= 1'b0;
                if (rvalid && rready) rvalid <= 1'b0;
            end
        end
    end

endmodule
