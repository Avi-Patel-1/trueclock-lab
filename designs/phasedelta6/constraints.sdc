create_clock -name clk -period $::env(CLOCK_PERIOD) [get_ports clk]
set_input_delay 1.0 -clock clk [get_ports {x* rst}]
set_output_delay 1.0 -clock clk [get_ports y*]
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y [get_ports {x* rst}]
set_input_transition 0.1 [get_ports clk]
set_load 0.033442 [get_ports y*]
set_max_fanout 10 [current_design]
set_max_transition 0.75 [current_design]
set_clock_uncertainty 0.25 [get_clocks clk]
set_clock_transition 0.15 [get_clocks clk]
if { [info exists ::env(OPENLANE_SDC_IDEAL_CLOCKS)] && $::env(OPENLANE_SDC_IDEAL_CLOCKS) } {
    unset_propagated_clock [all_clocks]
} else {
    set_propagated_clock [all_clocks]
}
