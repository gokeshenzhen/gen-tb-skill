# External AHB VIP reuse

`reuse_my_vip` is the intake path for users who already own an AHB VIP.
The generator imports that VIP without editing its source.

## Intake

```yaml
bus_protocol: ahb
ahb_vip_source: reuse_my_vip
ahb_vip_path: $PROJ_DIR/vip/user_ahb_vip
ahb_vip_reuse_level: import_only
```

Ask only for `ahb_vip_path` and one reuse-level choice:

- `import_only` (default): import the user's VIP into the compile tree;
  generated sanity and register-access tests continue to use `tb_api`
- `drive_with_vip`: Phase 5 may add project-local glue so the VIP can
  drive one minimal AHB read/write smoke sequence

## Scaffold Behavior

Scaffold scans `ahb_vip_path`, emits `tb/external_vip.f`, adds it to
`script/tb.f`, and skips `tb/ahb_agt_top/` plus the generated AHB RAL
adapter. User VIP files are read-only.

If the VIP needs a specific interface name, config key, package import,
or factory type, implement that as generated glue under `tb/`, `top/`,
or `test/` during compile-fix. Do not patch the VIP in place unless the
user explicitly changes scope to repairing the VIP itself.

