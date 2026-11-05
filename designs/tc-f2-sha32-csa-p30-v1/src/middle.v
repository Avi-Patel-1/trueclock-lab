module tc_schedule_middle (
    input wire [31:0] a, b, c, d,
    output wire [31:0] first, second
);
    wire [31:0] sum1, majority1, carry1, majority2;
    assign sum1 = a ^ b ^ c;
    assign majority1 = (a & b) | (a & c) | (b & c);
    assign carry1 = majority1 << 1;
    assign first = sum1 ^ carry1 ^ d;
    assign majority2 = (sum1 & carry1) | (sum1 & d) | (carry1 & d);
    assign second = majority2 << 1;
endmodule
