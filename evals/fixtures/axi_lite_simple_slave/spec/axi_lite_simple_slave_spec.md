# axi_lite_simple_slave

Minimal AXI4-Lite slave with two registers.

| Offset | Name    | Access | Reset       | Description           |
|--------|---------|--------|-------------|-----------------------|
| 0x000  | ID      | RO     | 0x000000A5  | Fixed identification  |
| 0x004  | SCRATCH | RW     | 0x00000000  | Scratch register      |
