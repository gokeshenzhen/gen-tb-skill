// Minimal AXI4-Lite master.
// After reset, issues a single write of 0xDEADBEEF to address 0x100, then idles.
module axi_lite_simple_master #(
    parameter ADDR_W = 12,
    parameter DATA_W = 32
) (
    input  wire              aclk,
    input  wire              aresetn,
    output reg               awvalid,
    input  wire              awready,
    output reg  [ADDR_W-1:0] awaddr,
    output reg  [2:0]        awprot,
    output reg               wvalid,
    input  wire              wready,
    output reg  [DATA_W-1:0] wdata,
    output reg  [3:0]        wstrb,
    input  wire              bvalid,
    output reg               bready,
    input  wire [1:0]        bresp,
    output reg               arvalid,
    input  wire              arready,
    output reg  [ADDR_W-1:0] araddr,
    output reg  [2:0]        arprot,
    input  wire              rvalid,
    output reg               rready,
    input  wire [DATA_W-1:0] rdata,
    input  wire [1:0]        rresp
);

    localparam STATE_IDLE = 3'd0;
    localparam STATE_ADDR = 3'd1;
    localparam STATE_WAIT = 3'd2;
    localparam STATE_RESP = 3'd3;
    localparam STATE_DONE = 3'd4;

    reg [2:0] state;
    reg       aw_done;
    reg       w_done;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            state    <= STATE_IDLE;
            awvalid  <= 1'b0;
            awaddr   <= {ADDR_W{1'b0}};
            awprot   <= 3'b000;
            wvalid   <= 1'b0;
            wdata    <= {DATA_W{1'b0}};
            wstrb    <= 4'h0;
            bready   <= 1'b0;
            arvalid  <= 1'b0;
            araddr   <= {ADDR_W{1'b0}};
            arprot   <= 3'b000;
            rready   <= 1'b0;
            aw_done  <= 1'b0;
            w_done   <= 1'b0;
        end else begin
            case (state)
                STATE_IDLE: begin
                    awaddr  <= 12'h100;
                    wdata   <= 32'hDEADBEEF;
                    wstrb   <= 4'hF;
                    awvalid <= 1'b1;
                    wvalid  <= 1'b1;
                    bready  <= 1'b1;
                    aw_done <= 1'b0;
                    w_done  <= 1'b0;
                    state   <= STATE_WAIT;
                end
                STATE_WAIT: begin
                    if (awvalid && awready) begin
                        awvalid <= 1'b0;
                        aw_done <= 1'b1;
                    end
                    if (wvalid && wready) begin
                        wvalid <= 1'b0;
                        w_done <= 1'b1;
                    end
                    if ((awready || aw_done) && (wready || w_done))
                        state <= STATE_RESP;
                end
                STATE_RESP: begin
                    if (bvalid && bready) begin
                        bready <= 1'b0;
                        state  <= STATE_DONE;
                    end
                end
                STATE_DONE: begin
                    // park
                end
                default: state <= STATE_IDLE;
            endcase
        end
    end

endmodule
