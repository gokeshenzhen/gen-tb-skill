// Minimal valid/ready streaming DUT, no registers.
// After reset, the DUT issues a single valid beat with data 0xDEADBEEF,
// then idles. Used as a generic-bus fixture (register_semantics: no,
// direction: master) for the gen-tb skill.
//
// Bus semantics (valid_ready):
//   tvalid_o high  -> DUT has payload ready
//   tdata_o        -> payload while tvalid_o is high
//   tready_i       -> downstream slave accepts (sampled on tvalid_o high)
//   tlast_o        -> packet boundary marker (always 1 for this DUT)
module generic_valid_ready_stream (
    input  wire        clk_i,
    input  wire        rst_n_i,            // active-low reset
    output reg         tvalid_o,
    input  wire        tready_i,
    output reg  [31:0] tdata_o,
    output reg         tlast_o
);

    reg done;

    always @(posedge clk_i or negedge rst_n_i) begin
        if (!rst_n_i) begin
            tvalid_o <= 1'b0;
            tdata_o  <= 32'h0;
            tlast_o  <= 1'b0;
            done     <= 1'b0;
        end else if (!done) begin
            tvalid_o <= 1'b1;
            tdata_o  <= 32'hDEAD_BEEF;
            tlast_o  <= 1'b1;
            if (tvalid_o && tready_i) begin
                done     <= 1'b1;
                tvalid_o <= 1'b0;
            end
        end else begin
            tvalid_o <= 1'b0;
        end
    end

endmodule
