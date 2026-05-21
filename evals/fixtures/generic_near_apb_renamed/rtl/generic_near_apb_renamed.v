// A near-APB-shaped register bus with renamed signals.
// Same APB ENABLE/READY semantics, but the ports are named after the
// vendor's internal convention so the scaffolder cannot pattern-match
// to APB by signature. Used as a generic-bus fixture exercising:
//
//   bus_protocol: generic
//   direction: slave
//   handshake.kind: req_ack       (sel+enable → req; ready → ack)
//   register_semantics: yes
//
// After reset:
//   - reg @0x00 returns 0x000000C3 on read (ID)
//   - reg @0x04 is a writable scratchpad (initial value 0)
module generic_near_apb_renamed (
    input  wire        bus_clk,
    input  wire        bus_rst_n,        // active-low
    input  wire        bus_sel,
    input  wire        bus_en,
    input  wire        bus_we,
    input  wire [11:0] bus_addr,
    input  wire [31:0] bus_wdata,
    output reg  [31:0] bus_rdata,
    output reg         bus_rdy
);

    reg [31:0] scratch;

    always @(posedge bus_clk or negedge bus_rst_n) begin
        if (!bus_rst_n) begin
            bus_rdata <= 32'h0;
            bus_rdy   <= 1'b0;
            scratch   <= 32'h0;
        end else begin
            if (bus_sel && bus_en && !bus_rdy) begin
                bus_rdy <= 1'b1;
                if (bus_we) begin
                    if (bus_addr[7:0] == 8'h04) scratch <= bus_wdata;
                end else begin
                    case (bus_addr[7:0])
                        8'h00:   bus_rdata <= 32'h0000_00C3;
                        8'h04:   bus_rdata <= scratch;
                        default: bus_rdata <= 32'h0;
                    endcase
                end
            end else begin
                bus_rdy <= 1'b0;
            end
        end
    end

endmodule
