# ahb_simple_master

Minimal AHB-Lite master. After reset, issues exactly one NONSEQ word
write (`HADDR=0x100`, `HWDATA=0xDEADBEEF`) and then drives `HTRANS=IDLE`.
Used as a DUT-as-master fixture for the gen-tb skill.
