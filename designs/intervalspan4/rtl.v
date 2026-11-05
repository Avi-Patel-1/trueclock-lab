// SPDX-License-Identifier: MIT
// Length of the intersection of two half-open intervals; inverted intervals are empty.
module intervalspan4(input clk, input rst, input [15:0] x, output reg [3:0] y);
    reg [15:0] xq;
    wire [3:0] a = xq[15:12], b = xq[11:8], c = xq[7:4], d = xq[3:0];
    wire [3:0] left = a > c ? a : c;
    wire [3:0] right = b < d ? b : d;
    always @(posedge clk) begin
        if (rst) begin
            xq <= 0;
            y <= 0;
        end else begin
            xq <= x;
            y <= right > left ? right - left : 4'd0;
        end
    end
endmodule
