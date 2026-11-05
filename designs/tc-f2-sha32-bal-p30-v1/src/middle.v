module tc_schedule_middle (
    input wire [31:0] a, b, c, d,
    output wire [31:0] first, second
);
    assign first = a + b;
    assign second = c + d;
endmodule
