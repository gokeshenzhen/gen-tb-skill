# RAL generation (gen-tb reference)

> Loaded during Phase 4. Defines how `registers.yaml` becomes the
> `tb/ral/<ip>_reg_block.sv` UVM RAL block.
>
> **Status: v1.2 full; v1.1 stub.** v1.1's `scripts/scaffold.py`
> emits a minimal `<ip>_reg_block.sv` with the class shell but no
> register instantiations. v1.1 reg_access_test uses direct `tb_api::read`
> on each yaml entry instead of RAL walks. Full RAL ships in v1.2;
> this file is the spec that implementation will follow.

## Goal of the generated RAL

Produce a `<ip>_reg_block` that lets DV-persona code do:

```systemverilog
ral.CTRL.write(status, 32'h5);          // typed access, no raw addr
ral.STATUS.mirror(status);              // backdoor / mirror
ral.KEY[i].write(status, key[i]);       // array access
uvm_reg_seq_lib::reg_access_seq seq;    // built-in walk-test
```

…while keeping the address constants in sync with what `tb_api::read`
sees. Both paths must produce the same APB transactions.

## Per-register emission

For each `registers.yaml` entry where `aliased_by == null` AND
`array_of == null`:

```systemverilog
class <IP>_<REG>_reg extends uvm_reg;
    `uvm_object_utils(<IP>_<REG>_reg)
    rand uvm_reg_field <field_name>;       // one per field
    function new(string name = "<IP>_<REG>_reg");
        super.new(name, <reg.width>, UVM_NO_COVERAGE);
    endfunction
    virtual function void build();
        <field_name> = uvm_reg_field::type_id::create("<field_name>");
        <field_name>.configure(
            this,                  // parent reg
            <field.width>,         // bits
            <field.lsb>,           // lsb position
            "<field.access>",      // UVM 1.2 field access policy
            0,                     // volatile
            <field.reset>,         // reset value
            1,                     // has_reset
            1,                     // is_rand
            1                      // individually_accessible
        );
        // ... more fields ...
    endfunction
endclass
```

The block ties them together:

```systemverilog
class <ip>_reg_block extends uvm_reg_block;
    `uvm_object_utils(<ip>_reg_block)
    rand <IP>_CTRL_reg   CTRL;
    rand <IP>_STATUS_reg STATUS;
    // ... more regs ...
    function new(string name = "<ip>_reg_block");
        super.new(name, UVM_NO_COVERAGE);
    endfunction
    virtual function void build();
        default_map = create_map("default_map", 0, 4, UVM_LITTLE_ENDIAN);
        CTRL = <IP>_CTRL_reg::type_id::create("CTRL");
        CTRL.configure(this, null, "");
        CTRL.build();
        default_map.add_reg(CTRL, 12'h020, "RW"); // RO | RW | WO map rights
        // ... more regs ...
        lock_model();
    endfunction
endclass
```

## Aliased registers (`aliased_by` non-null)

Three regs share offset 0x00 (RBR/THR/DLL) — RAL emits three
separate `uvm_reg` classes, all mapped to the same offset. The
**default `reg_access_test` skips them** by checking
`r.get_n_used_hdl_paths()` or a custom marker via
`uvm_resource_db`. A bank-aware test goes into
`<ip>_bank_access_test.sv` (currently a stub generated alongside
reg_access_test).

```systemverilog
default_map.add_reg(RBR, 12'h000, "RO");
default_map.add_reg(THR, 12'h000, "WO");
default_map.add_reg(DLL, 12'h000, "RW");
```

UVM permits multiple `uvm_reg`s at the same offset — `add_reg` does
not deduplicate. The downside is that `reg_access_seq` will randomly
write to any of them. Mitigation: gen-tb emits a small subclass of
`reg_access_seq` that filters by `aliased_by != null`:

```systemverilog
class <ip>_reg_access_seq extends uvm_reg_seq_lib::reg_access_seq;
    virtual function bit pre_access_register(uvm_reg rg);
        // Skip aliased registers; the generated <ip>_bank_access_test
        // exercises them explicitly.
        return !rg.get_attribute("aliased") == "1";
    endfunction
endclass
```

In each aliased `uvm_reg`'s `build()`:
```systemverilog
set_attribute("aliased", "1");
```

## Disjoint RO/WO at same offset (no `aliased_by`)

Two regs share offset 0x08 (IIR=RO, FCR=WO), but bank-select is N/A
— access mode disambiguates. RAL emits both. The default
`reg_access_seq` does write-then-read; for an RO reg the write is a
no-op (UVM treats WR to RO as silent), so the read still goes
through correctly. For the WO partner, the read is skipped.

`reg_access_test` works as-is on this pair, **provided each `uvm_reg`'s
field-level access mirrors the access pair**. Validation:

```systemverilog
// IIR — read-only at offset 0x08
IIR = <IP>_IIR_reg::type_id::create("IIR");   // fields all "RO"
default_map.add_reg(IIR, 12'h008, "RO");

// FCR — write-only at offset 0x08
FCR = <IP>_FCR_reg::type_id::create("FCR");   // fields all "WO"
default_map.add_reg(FCR, 12'h008, "WO");
```

The `add_reg` access string (3rd arg) is **gen-tb's hint to the
walk-test**, not enforced by UVM. The field-level access strings ARE
enforced.

## Register arrays (`array_of` non-null)

Emit a Verilog-array of `uvm_reg`s:

```systemverilog
rand <IP>_KEY_reg KEY[4];

// in build():
foreach (KEY[i]) begin
    KEY[i] = <IP>_KEY_reg::type_id::create($sformatf("KEY[%0d]", i));
    KEY[i].configure(this, null, "");
    KEY[i].build();
    default_map.add_reg(KEY[i], 12'h040 + i*4, "WO");
end
```

DV code can now write `ral.KEY[2].write(status, value)` or iterate
`foreach (ral.KEY[i])`. The `tb_api::write_array` helper is the
DE-persona equivalent and uses the same byte addresses.

## Reset value handling

Each field's `reset` from yaml drops into `configure()`'s 6th arg.
**Field-level resets win over register-level** (per
`registers_yaml_schema.md` G8 rule). When a register has multiple
fields, sum is OR-of-(field.reset << field.lsb) — emit that as the
`uvm_reg::reset()` value where applicable.

## Adapter for APB

```systemverilog
class <ip>_apb_adapter extends uvm_reg_adapter;
    `uvm_object_utils(<ip>_apb_adapter)
    function new(string name = "<ip>_apb_adapter");
        super.new(name);
        supports_byte_enable = 0;
        provides_responses   = 1;
    endfunction
    virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
        apb_trans t = apb_trans::type_id::create("t");
        t.addr  = rw.addr;
        t.data  = rw.data;
        t.write = (rw.kind == UVM_WRITE);
        return t;
    endfunction
    virtual function void bus2reg(uvm_sequence_item bus_item, ref uvm_reg_bus_op rw);
        apb_trans t;
        $cast(t, bus_item);
        rw.kind   = t.write ? UVM_WRITE : UVM_READ;
        rw.addr   = t.addr;
        rw.data   = t.write ? t.data : t.rdata;
        rw.status = t.slverr ? UVM_NOT_OK : UVM_IS_OK;
    endfunction
endclass
```

This connects the RAL to the generated bus agent's sequencer:

```systemverilog
// in env.connect_phase
ral.default_map.set_sequencer(agent.sqr, adapter);
ral.default_map.set_auto_predict(1);
```

## What v1.1 emits

```systemverilog
// minimal stub — replaces with full impl in v1.2
class <ip>_reg_block extends uvm_reg_block;
    `uvm_object_utils(<ip>_reg_block)
    function new(string name="<ip>_reg_block");
        super.new(name, UVM_NO_COVERAGE);
    endfunction
    virtual function void build();
        // TODO(v1.2): instantiate <N> uvm_reg objects per registers.yaml
        default_map = create_map("default_map", 0, 4, UVM_LITTLE_ENDIAN);
        lock_model();
    endfunction
endclass
```

The stub compiles, satisfies `uvm_reg_block` contract sufficiently for
elaboration, and lets v1.2's drop-in upgrade happen without touching
the test layer.

## Cross-references

- `references/registers_yaml_schema.md` — the input format
- `references/apb.md` — the agent the adapter connects to
- `references/tb_api.md` — the DE-persona equivalent path
- `scripts/scaffold.py` `emit_ral_block` (v1.1 stub; v1.2 full)
