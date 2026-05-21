// Minimal req/ack-handshake DUT with one read-only register.
// Used as a generic-bus fixture for the gen-tb skill.
//
// Bus semantics (req_ack):
//   stb_i high  -> assert req
//   ack_o pulses high one cycle after stb_i with data on dat_o for reads.
//   we_i high   -> write (data on dat_i)
//   we_i low    -> read  (returns 0x000000A5 from any address)
module generic_req_ack_simple (
    input  wire        clk_i,
    input  wire        rst_i,           // active-high reset
    input  wire [11:0] adr_i,
    input  wire [31:0] dat_i,
    output reg  [31:0] dat_o,
    input  wire        stb_i,
    output reg         ack_o,
    input  wire        we_i,
    input  wire        cyc_i
);

    always @(posedge clk_i or posedge rst_i) begin
        if (rst_i) begin
            ack_o <= 1'b0;
            dat_o <= 32'h0000_0000;
        end else begin
            if (stb_i && cyc_i && !ack_o) begin
                ack_o <= 1'b1;
                if (!we_i) dat_o <= 32'h0000_00A5;
            end else begin
                ack_o <= 1'b0;
            end
        end
    end

endmodule
