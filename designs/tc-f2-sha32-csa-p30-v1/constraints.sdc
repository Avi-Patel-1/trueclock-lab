create_clock -period $::env(CLOCK_PERIOD) -name clk [get_ports clk]
set clk [get_clocks clk]
set data_ports [get_ports {rst in_valid w_m16* w_m15* w_m7* w_m2*}]
set_input_delay 2.0 -clock $clk $data_ports
set_output_delay 2.0 -clock $clk [all_outputs]
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y $data_ports
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y [get_ports clk]
set_load 0.033442 [all_outputs]
set_max_fanout 10 [current_design]
set_max_transition 0.75 [current_design]
if {[info exists ::env(MAX_CAPACITANCE_CONSTRAINT)]} {
    set_max_capacitance $::env(MAX_CAPACITANCE_CONSTRAINT) [current_design]
}
set_clock_uncertainty 0.25 $clk
set_clock_transition 0.15 $clk
set_timing_derate -early [expr {1.0 - $::env(TIME_DERATING_CONSTRAINT) / 100.0}]
set_timing_derate -late [expr {1.0 + $::env(TIME_DERATING_CONSTRAINT) / 100.0}]
if {[info exists ::env(OPENLANE_SDC_IDEAL_CLOCKS)] && $::env(OPENLANE_SDC_IDEAL_CLOCKS)} {
    unset_propagated_clock [all_clocks]
} else {
    set_propagated_clock [all_clocks]
}
