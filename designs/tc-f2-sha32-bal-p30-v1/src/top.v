// One SHA-256 message schedule update. An input captured at k emerges at k+2.
module tc_sha256_schedule (
    input wire clk,
    input wire rst,
    input wire in_valid,
    input wire [31:0] w_m16,
    input wire [31:0] w_m15,
    input wire [31:0] w_m7,
    input wire [31:0] w_m2,
    output reg out_valid,
    output reg [31:0] out_word
);
    reg [31:0] m16_q, m15_q, m7_q, m2_q;
    reg [31:0] first_q, second_q;
    reg valid_q, valid_mid;
    wire [31:0] sigma0 = {m15_q[6:0], m15_q[31:7]} ^
                         {m15_q[17:0], m15_q[31:18]} ^ (m15_q >> 3);
    wire [31:0] sigma1 = {m2_q[16:0], m2_q[31:17]} ^
                         {m2_q[18:0], m2_q[31:19]} ^ (m2_q >> 10);
    wire [31:0] first, second;

    tc_schedule_middle arithmetic (
        .a(m16_q), .b(sigma0), .c(m7_q), .d(sigma1),
        .first(first), .second(second)
    );

    always @(posedge clk) begin
        if (rst) begin
            m16_q <= 32'd0;
            m15_q <= 32'd0;
            m7_q <= 32'd0;
            m2_q <= 32'd0;
            first_q <= 32'd0;
            second_q <= 32'd0;
            out_word <= 32'd0;
            valid_q <= 1'b0;
            valid_mid <= 1'b0;
            out_valid <= 1'b0;
        end else begin
            m16_q <= w_m16;
            m15_q <= w_m15;
            m7_q <= w_m7;
            m2_q <= w_m2;
            first_q <= first;
            second_q <= second;
            out_word <= first_q + second_q;
            valid_q <= in_valid;
            valid_mid <= valid_q;
            out_valid <= valid_mid;
        end
    end
endmodule
