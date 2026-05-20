# External AXI4-Lite VIP reuse

`reuse_my_vip` is the intake path for users who already own an
AXI4-Lite VIP. The generator imports that VIP without editing its
source.

## Intake

```yaml
bus_protocol: axi_lite
bus_direction: slave                    # or master
axi_lite_vip_source: reuse_my_vip
axi_lite_vip_path: $PROJ_DIR/vip/user_axi_lite_vip
axi_lite_vip_reuse_level: import_only
```

Ask only for `axi_lite_vip_path` and one reuse-level choice:

- `import_only` (default): import the user's VIP into the compile tree;
  generated sanity/reg_access (slave direction) or responder_smoke
  (master direction) continue to use `tb_api`
- `drive_with_vip`: Phase 5 may add project-local glue so the VIP can
  drive (or respond to) one minimal AXI4-Lite read/write smoke sequence

## Scaffold Behavior

Scaffold scans `axi_lite_vip_path`, emits `tb/external_vip.f`, adds it
to `script/tb.f`, and skips `tb/axi_lite_agt_top/` plus the generated
AXI4-Lite RAL adapter. User VIP files are read-only.

For `import_only`, scaffold imports interface and agent/package compile
units but does not compile obvious vendor test packages such as
`*_test_pkg.sv`. Those packages often re-include internal env/test
files and assume the VIP's standalone top. Generated sanity / register
/ responder tests should stay on `tb_api` until a project-local
`drive_with_vip` glue layer is added.

For `bus_direction: master`, the user VIP must supply a slave responder
agent; gen-tb does not auto-build a memory-backed responder from a
master-only third-party VIP. The combination
`bus_direction: master + axi_lite_vip_source: reuse_my_vip +
axi_lite_vip_reuse_level: import_only` is explicitly rejected by
`scaffold.py` — the built-in `<ip>_responder_smoke_test` relies on the
generated responder to populate `tb_api::_mem` and `writes_observed`,
which `import_only` skips. Use `drive_with_vip` (Phase 5 generates
glue against the user VIP) or `generate_fresh` instead.

If the VIP needs a specific interface name, config key, package import,
or factory type, implement that as generated glue under `tb/`, `top/`,
or `test/` during compile-fix. Do not patch the VIP in place unless the
user explicitly changes scope to repairing the VIP itself.
