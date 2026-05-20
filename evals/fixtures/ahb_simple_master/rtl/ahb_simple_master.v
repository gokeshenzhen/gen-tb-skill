// Minimal AHB-Lite master.
// After reset, issues a single NONSEQ word write of 0xDEADBEEF to address
// 0x100, then idles (htrans=IDLE).
module ahb_simple_master #(
    parameter ADDR_W = 12,
    parameter DATA_W = 32
) (
    input  wire              hclk,
    input  wire              hresetn,
    output reg               hsel,
    output reg  [ADDR_W-1:0] haddr,
    output reg  [1:0]        htrans,
    output reg               hwrite,
    output reg  [2:0]        hsize,
    output reg  [2:0]        hburst,
    output reg  [3:0]        hprot,
    output reg  [DATA_W-1:0] hwdata,
    input  wire [DATA_W-1:0] hrdata,
    input  wire              hready,
    input  wire              hresp
);

    localparam [1:0] HTRANS_IDLE   = 2'b00;
    localparam [1:0] HTRANS_NONSEQ = 2'b10;

    localparam [1:0] S_RESET = 2'd0;
    localparam [1:0] S_ADDR  = 2'd1;
    localparam [1:0] S_DATA  = 2'd2;
    localparam [1:0] S_DONE  = 2'd3;

    reg [1:0] state;

    always @(posedge hclk or negedge hresetn) begin
        if (!hresetn) begin
            state  <= S_RESET;
            hsel   <= 1'b0;
            haddr  <= {ADDR_W{1'b0}};
            htrans <= HTRANS_IDLE;
            hwrite <= 1'b0;
            hsize  <= 3'b010;   // word
            hburst <= 3'b000;   // single
            hprot  <= 4'b0011;
            hwdata <= {DATA_W{1'b0}};
        end else begin
            case (state)
                S_RESET: begin
                    // Drive NONSEQ address phase.
                    hsel   <= 1'b1;
                    haddr  <= 12'h100;
                    htrans <= HTRANS_NONSEQ;
                    hwrite <= 1'b1;
                    hsize  <= 3'b010;
                    hburst <= 3'b000;
                    hprot  <= 4'b0011;
                    state  <= S_ADDR;
                end
                S_ADDR: begin
                    if (hready) begin
                        // Move to data phase; deassert htrans/hsel for a
                        // single-beat transfer with no follow-up.
                        htrans <= HTRANS_IDLE;
                        hsel   <= 1'b0;
                        hwdata <= 32'hDEADBEEF;
                        state  <= S_DATA;
                    end
                end
                S_DATA: begin
                    if (hready) begin
                        state <= S_DONE;
                    end
                end
                S_DONE: begin
                    htrans <= HTRANS_IDLE;
                    hsel   <= 1'b0;
                end
                default: state <= S_RESET;
            endcase
        end
    end

endmodule
