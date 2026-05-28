# RTL discovery schema (gen-tb reference)

> Loaded during Phase 1 discovery and Phase 4 scaffold. Defines
> `work/_gen_audit/rtl_discovery.yaml`, the bridge between user RTL and
> generated `script/design.f` / `top/<ip>_tb_top.sv`.

## Required Output

Write:

```text
work/_gen_audit/rtl_discovery.yaml
```

This file records the exact RTL files, top module, bus signal names,
non-bus pads, and any wrapper address mapping. It is generated from user
RTL or user filelists. Do not edit user RTL to make discovery easier.

For built-in buses (APB / AHB-Lite / AXI4-Lite) it carries the bus
interface block below. For any other bus the DUT falls through to
generic mode — `rtl_discovery.yaml` then carries a `generic_interface`
stub and the per-IP handshake is described separately in
`work/_gen_audit/bus_handshake.yaml` (see `references/generic_bus.md`).

## Minimal v1.2 Schema

```yaml
mode: scan                         # scan | filelist | user_provided
ip_name: uart16550
ip_root: /abs/path/to/ip
rtl_dir: $PROJ_DIR/rtl
filelist_origin: generated         # generated | user_filelist | user_answer

top_module:
  name: uart_apb_wrap
  file: $PROJ_DIR/rtl/uart_apb_wrap.v
  confidence: medium               # high | medium | low
  method: regex_topology           # optional, see "Top inference"

files:
  - {path: $PROJ_DIR/rtl/timescale.v, role: include, order: 1}
  - {path: $PROJ_DIR/rtl/uart_regs.v, role: leaf, order: 2}
  - {path: $PROJ_DIR/rtl/uart_apb_wrap.v, role: top, order: 20}

apb_interface:                    # use ahb_interface when bus_protocol: ahb
  pclk: pclk
  presetn: presetn
  psel: psel
  penable: penable
  pwrite: pwrite
  paddr:  {name: paddr,  width: 12}
  pwdata: {name: pwdata, width: 32}
  prdata: {name: prdata, width: 32}
  pready: pready
  pslverr: pslverr

other_pads:
  - {name: irq, dir: out, role: interrupt}
  - {name: srx_pad_i, dir: in, role: serial_rx, default: "1'b1"}

wrapper_address_map:
  status: inferred                 # none | inferred | explicit | unknown
  programmer_offset_unit: byte     # byte | word
  dut_addr_expr: "paddr[4:2]"      # optional expression observed in wrapper
  yaml_offset_semantics: post_wrapper_paddr
  notes:
    - "APB byte offset N*4 maps to inner word index N."
```

For AHB-Lite, use:

```yaml
ahb_interface:
  direction: slave              # slave (DUT is slave) | master (DUT is master)
  hclk: hclk
  hresetn: hresetn
  hsel: hsel
  haddr:  {name: haddr,  width: 12}
  htrans: htrans
  hwrite: hwrite
  hsize: hsize
  hburst: hburst
  hprot: hprot
  hwdata: {name: hwdata, width: 32}
  hrdata: {name: hrdata, width: 32}
  hready: hready
  hresp: hresp
```

For AXI4-Lite, use:

```yaml
axi_lite_interface:
  direction: slave              # slave (DUT is slave) | master (DUT is master)
  aclk: aclk
  aresetn: aresetn
  awvalid: awvalid
  awready: awready
  awaddr:  {name: awaddr,  width: 12}
  awprot:  awprot
  wvalid:  wvalid
  wready:  wready
  wdata:   {name: wdata,  width: 32}
  wstrb:   {name: wstrb,  width: 4}
  bvalid:  bvalid
  bready:  bready
  bresp:   bresp
  arvalid: arvalid
  arready: arready
  araddr:  {name: araddr,  width: 12}
  arprot:  arprot
  rvalid:  rvalid
  rready:  rready
  rdata:   {name: rdata,  width: 32}
  rresp:   rresp
```

`direction` controls which side of the bus the TB owns; it is
load-bearing for both `tb_api` primitives and the generated agent
(master BFM vs slave responder).

## Bus Classification (Phase 1)

After recording files and the top module, classify the bus from the
top module's port signature and record the result:

```yaml
classified_bus: apb | ahb | axi_lite | unknown
```

- A clear APB / AHB-Lite / AXI4-Lite signature → that `bus_protocol`.
- No match → `bus_protocol: unknown`; the bus is handled in generic
  mode. Emit a `candidate_signals` list (the request/response-looking
  port group) so the intake step can build `bus_handshake.yaml`.

```yaml
generic_interface:
  note: ports described in work/_gen_audit/bus_handshake.yaml
  candidate_signals: [stb_i, ack_o, adr_i, dat_i, dat_o, we_i, cyc_i]
```

### AXI4-full detector

When the bus classifies as `axi_lite` but the top module also exposes
full-AXI burst/ID ports (`awlen`, `arlen`, `awburst`, `arburst`,
`awid`, `arid` — width > 0), record:

```yaml
axi_full_signature: true
axi_full_id_width: 4          # derived from the awid/arid declaration
```

AXI4 full (real bursts / IDs / outstanding) is out of scope. The
Phase 2 mandatory question asks whether the DUT actually uses bursts:

- **Yes** → hard refuse, point to planned AXI4-full work.
- **No** (single-beat only) → generate the AXI4-Lite environment in
  **degraded mode**: the interface carries the burst/ID ports plus
  `AWLEN == 0 && ARLEN == 0` concurrent assertions, and the TB master
  ties the burst signals to single-beat values. See
  `references/axi_lite.md` → "AXI4-full degraded mode".

`classify_bus`, `detect_axi_full`, and `candidate_signals` are
functions in `scripts/discover_inputs.py`.

`scripts/scaffold.py` currently consumes `top_module.name`,
`files[*].path`, `rtl_dir`, the bus interface block, and `other_pads`.
`wrapper_address_map` is documentation/audit today; future parser
scripts should use it to cross-check `registers.yaml` offsets.

## File Discovery

Search in this order:

1. User-provided filelist (`*.f`, `*.flist`, Makefile variable, or
   explicit path)
2. `rtl/`, `src/`, `design/`, `hdl/`
3. IP root subdirectories that are not `tb/`, `test/`, `sim/`,
   `bench/`, `verification/`, `work/`, or `.git/`

Preserve user filelist order when one exists. For scanned RTL, emit a
best-effort leaf-before-top order and record `filelist_origin:
generated`. Keep include files and `+incdir+` directories in the same
relative order when discovered from a filelist.

Use `$PROJ_DIR`-relative paths in emitted yaml whenever files are inside
the generated IP root. For external read-only RTL, keep absolute paths
and mark them in `parse_report.md` or `rtl_discovery.yaml.notes`.

## Top Inference

Prefer structural tools over regex:

| Method | Confidence | Notes |
|---|---|---|
| user answer / explicit filelist top | high | User names the top or filelist has a single obvious top |
| slang AST/elaboration | high | Best automated choice when available |
| verible-verilog-syntax + module graph | medium-high | Good syntax-level graph, no full elaboration |
| regex module/instantiation graph | medium | Current fallback used by eval harness |
| filename heuristic only | low | Ask user before scaffold |

If multiple candidate tops remain, prefer wrappers whose module/file name
contains the selected bus (`apb`, `ahb`), `<bus>_wrap`, `reg_if`, or the IP name. If still
ambiguous, ask the user. Do not silently pick a random uninstantiated
module.

Record:

```yaml
top_module:
  confidence: high | medium | low
  method: user_answer | slang | verible | regex_topology | filename
  candidates: [mod_a, mod_b]       # optional when ambiguous
```

## APB Port Discovery

The generated top expects APB-slave semantics:

- one clock: `pclk`
- one active-low reset: `presetn`
- APB control: `psel`, `penable`, `pwrite`
- address: `paddr`
- write data: `pwdata`
- read data: `prdata`
- response: `pready`, `pslverr`

Do not normalize case in the emitted names; use the exact RTL port
names. Match common variants only for discovery:

| Canonical | Common variants |
|---|---|
| `pclk` | `PCLK`, `clk`, `apb_clk` |
| `presetn` | `PRESETn`, `preset_n`, `rst_n`, `resetn` |
| `psel` | `PSEL`, `psel_i`, `apb_psel` |
| `penable` | `PENABLE`, `penable_i` |
| `pwrite` | `PWRITE`, `pwrite_i`, `wr` |
| `paddr` | `PADDR`, `addr`, `apb_addr` |
| `pwdata` | `PWDATA`, `wdata`, `apb_wdata` |
| `prdata` | `PRDATA`, `rdata`, `apb_rdata` |
| `pready` | `PREADY`, `ready`, `apb_ready` |
| `pslverr` | `PSLVERR`, `slverr`, `error` |

If `pready` or `pslverr` is absent but the RTL is otherwise APB-like,
record them in `unmatched_roles` and proceed — scaffold ties the
interface signal to the protocol-idle default (`pready = 1'b1` for
zero-wait-state, `pslverr = 1'b0` for no-error) at the top level
instead of wiring a DUT port. The driver/monitor/tb_api code is
unchanged: `pready` being constantly high makes the wait-for-ready
loops fall through after the standard two-phase APB cycle.

## AHB Port Discovery

The generated top uses the same AHB-Lite signal set regardless of
`bus_direction` (slave or master) — only the role of TB vs DUT
changes:

- one clock: `hclk`
- one active-low reset: `hresetn`
- control: `hsel`, `htrans`, `hwrite`, `hsize`, `hburst`, `hprot`
- address: `haddr`
- write data: `hwdata`
- read data: `hrdata`
- response/ready: `hready`, `hresp`

Record the chosen direction in `ahb_interface.direction`
(`slave` is the default; `master` means the DUT initiates transfers
and the TB provides a memory-backed slave responder).

Do not normalize case in the emitted names; use exact RTL port names.
Match common variants only for discovery:

| Canonical | Common variants |
|---|---|
| `hclk` | `HCLK`, `clk`, `ahb_clk` |
| `hresetn` | `HRESETn`, `hreset_n`, `rst_n`, `resetn` |
| `hsel` | `HSEL`, `hsel_i`, `ahb_hsel` |
| `htrans` | `HTRANS`, `htrans_i` |
| `hwrite` | `HWRITE`, `hwrite_i`, `wr` |
| `haddr` | `HADDR`, `addr`, `ahb_addr` |
| `hsize` | `HSIZE`, `hsize_i` |
| `hburst` | `HBURST`, `hburst_i` |
| `hprot` | `HPROT`, `hprot_i` |
| `hwdata` | `HWDATA`, `wdata`, `ahb_wdata` |
| `hrdata` | `HRDATA`, `rdata`, `ahb_rdata` |
| `hready` | `HREADY`, `ready`, `ahb_ready` |
| `hresp` | `HRESP`, `resp`, `error` |

## AXI4-Lite Port Discovery

The generated top expects AXI4-Lite signals across five channels (AW /
W / B / AR / R), independent of `bus_direction`:

- clock/reset: `aclk`, `aresetn`
- AW: `awvalid`, `awready`, `awaddr`, `awprot`
- W:  `wvalid`,  `wready`,  `wdata`,  `wstrb`
- B:  `bvalid`,  `bready`,  `bresp`
- AR: `arvalid`, `arready`, `araddr`, `arprot`
- R:  `rvalid`,  `rready`,  `rdata`,  `rresp`

`bus_direction` is recorded in `axi_lite_interface.direction` and
determines which side of the bus the TB owns (`slave` → DUT is slave,
TB master BFM; `master` → DUT is master, TB slave responder). The
signal map itself does not change.

Common variants to match during discovery: any of the canonical names
plus `_i`/`_o` suffixes, all-caps forms (`AWVALID` …), or AXI4 names
with `axi_` prefix (`axi_awvalid` …). Do not normalize case in the
emitted names. AXI4 burst/ID signals (`awlen`, `awid`, …), if present,
set `axi_full_signature: true` — see "AXI4-full detector" above; the
Phase 2 mandatory question then decides hard-refuse vs AXI4-Lite
degraded mode.

## Non-bus Pads

Every top-level port not consumed by APB clock/reset/data must appear in
`other_pads`.

```yaml
other_pads:
  - name: irq
    dir: out
    role: interrupt
  - name: srx_pad_i
    dir: in
    role: serial_rx
    default: "1'b1"
```

Direction is from the DUT point of view. Generated `top.sv` ties input
pads to protocol-idle defaults and declares output pads as wires.
Conservative defaults:

| Role | Default |
|---|---|
| serial_rx, modem, flow_ctrl input | `1'b1` |
| enable/config input | `1'b0` |
| scan/test input | ask user |
| clock/reset input outside APB | ask user |

If a pad is required for functional smoke testing, record it in
`behavior.md` as well.

## Wrapper Address Mapping (G21/G22)

`registers.yaml` offsets are always the programmer-visible APB byte
offsets driven on `paddr`. Some wrappers translate those bits before
reaching the inner IP, for example:

```systemverilog
.wb_addr_i(paddr[4:2])
```

That means APB offsets `0x00`, `0x04`, `0x08` map to inner word indices
`0`, `1`, `2`. The normalized register table must keep `0x00`, `0x04`,
`0x08`; do not rewrite it to the inner bus index.

Record the observation:

```yaml
wrapper_address_map:
  status: inferred
  programmer_offset_unit: byte
  dut_addr_expr: "paddr[4:2]"
  inner_offset_unit: word
  scale: 4
  yaml_offset_semantics: post_wrapper_paddr
```

If the wrapper uses arbitrary decode logic, set `status: unknown` and
warn in `parse_report.md`. Scaffold can still proceed if
`registers.yaml` already uses APB-visible offsets.

## Validation Checks

Before Phase 4:

- every file in `files` exists
- `top_module.file` exists and contains `module <name>`
- APB widths fit `intake.yaml.paddr_width` and generated data width
- every non-APB top-level port is listed in `other_pads`
- largest `registers.yaml` offset fits `paddr.width`
- wrapper mapping does not contradict `registers.yaml` offset semantics

Blocking failures require a user answer or a corrected input file.

## Cross-references

- `references/spec_parsing.md` — register offsets and parse reports
- `references/registers_yaml_schema.md` — post-wrapper offset semantics
- `references/top_sv.md` — generated top wiring rules
- `scripts/run_evals.py` `_emit_rtl_discovery` — current eval fallback
