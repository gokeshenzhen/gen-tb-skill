// Minimal AXI4-Lite slave with two registers (no bursts, no outstanding).
//   0x000: ID register, reset 0x000000A5, RO
//   0x004: SCRATCH register, RW
module axi_lite_simple_slave #(
    parameter ADDR_W = 12,
    parameter DATA_W = 32
) (
    input  wire              aclk,
    input  wire              aresetn,
    input  wire              awvalid,
    output reg               awready,
    input  wire [ADDR_W-1:0] awaddr,
    input  wire [2:0]        awprot,
    input  wire              wvalid,
    output reg               wready,
    input  wire [DATA_W-1:0] wdata,
    input  wire [3:0]        wstrb,
    output reg               bvalid,
    input  wire              bready,
    output reg  [1:0]        bresp,
    input  wire              arvalid,
    output reg               arready,
    input  wire [ADDR_W-1:0] araddr,
    input  wire [2:0]        arprot,
    output reg               rvalid,
    input  wire              rready,
    output reg  [DATA_W-1:0] rdata,
    output reg  [1:0]        rresp
);

    localparam [DATA_W-1:0] ID_RESET = 32'h000000A5;

    reg [DATA_W-1:0] scratch;
    reg [ADDR_W-1:0] aw_addr_r;
    reg              aw_seen;
    reg              w_seen;

    // Write address channel
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            awready   <= 1'b0;
            aw_addr_r <= {ADDR_W{1'b0}};
            aw_seen   <= 1'b0;
        end else begin
            if (!aw_seen && awvalid && !awready) begin
                awready   <= 1'b1;
                aw_addr_r <= awaddr;
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
        end else begin
            if (aw_seen && w_seen && !bvalid) begin
                bvalid <= 1'b1;
                bresp  <= 2'b00;
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
        end else begin
            if (arvalid && !arready && !rvalid) begin
                arready <= 1'b1;
                rvalid  <= 1'b1;
                rresp   <= 2'b00;
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
