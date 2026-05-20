# axi_lite_simple_master

Minimal AXI4-Lite master. After reset, issues exactly one write
transaction (`AW=0x100, W=0xDEADBEEF`) and then idles. Used as a
DUT-as-master fixture for the gen-tb skill.
