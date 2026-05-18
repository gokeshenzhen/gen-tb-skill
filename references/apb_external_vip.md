# External APB VIP reuse (v1.2)

`reuse_my_vip` is the intake path for users who already own an APB VIP
and do not want gen-tb to generate a second one.

## Intake contract

```yaml
apb_vip_source: reuse_my_vip
apb_vip_path: $PROJ_DIR/vip/user_apb_vip
```

The skill should ask only for `apb_vip_path` when the user says they
already have an APB VIP. `scaffold.py` resolves `$PROJ_DIR`, scans the
directory, and records what it found in `scaffold_audit.json`.

## What scaffold.py does

1. Scans `*.sv` files for:
   - `package ...;`
   - classes extending `uvm_agent`
   - classes extending `uvm_sequence_item`
   - config-like `uvm_object` classes (`cfg` / `config` in the name)
   - `interface ...`
2. Emits `tb/external_vip.f` with the required `+incdir+` entries and
   compile units.
3. Adds `-f $PROJ_DIR/tb/external_vip.f` to `script/tb.f`.
4. Skips fresh `tb/apb_agt_top/` generation.
5. Keeps `sanity_test` and `reg_access_test` on `tb_api`, so importing a
   user VIP does not require gen-tb to guess that VIP's runtime API.

## Current boundary

v1.2 reuse is **direct import**, not universal auto-adaptation. It gets
the user's VIP into the generated project and keeps the basic testbench
runnable without rewriting their files.

Gen-tb does not yet auto-wire arbitrary third-party agent/config/sequence
APIs into a generated `random_seq_test`. That needs an explicit adapter
contract because external VIPs routinely differ on config keys, vif
types, sequencer field names, and transaction schemas.
