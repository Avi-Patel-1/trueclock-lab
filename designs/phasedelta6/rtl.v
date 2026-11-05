// SPDX-License-Identifier: MIT
// Shortest distance between two positions on a ring with 64 positions.
module phasedelta6(input clk, input rst, input [11:0] x, output reg [5:0] y);
    reg [11:0] xq;
    wire [5:0] clockwise = xq[11:6] - xq[5:0];
    wire [5:0] counterclockwise = xq[5:0] - xq[11:6];
    always @(posedge clk) begin
        if (rst) begin
            xq <= 0;
            y <= 0;
        end else begin
            xq <= x;
            y <= clockwise < counterclockwise ? clockwise : counterclockwise;
        end
    end
endmodule
