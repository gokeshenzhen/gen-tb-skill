package user_axi_lite_pkg;
    import uvm_pkg::*;
    `include "uvm_macros.svh"

    class user_axi_lite_trans extends uvm_sequence_item;
        `uvm_object_utils(user_axi_lite_trans)
        rand logic [11:0] addr;
        rand logic [31:0] data;
        rand bit          write;
        function new(string name = "user_axi_lite_trans"); super.new(name); endfunction
    endclass

    class user_axi_lite_cfg extends uvm_object;
        `uvm_object_utils(user_axi_lite_cfg)
        function new(string name = "user_axi_lite_cfg"); super.new(name); endfunction
    endclass

    class user_axi_lite_agent extends uvm_agent;
        `uvm_component_utils(user_axi_lite_agent)
        function new(string name, uvm_component parent = null);
            super.new(name, parent);
        endfunction
    endclass
endpackage
