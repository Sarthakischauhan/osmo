# Vendored code

## `vendor/core_server/`

An unmodified copy of Symphony's `core_server` package, vendored because it is
not published to PyPI yet.

| | |
| --- | --- |
| Source repo | https://github.com/Sarthakischauhan/symphony |
| Source path | `core_server/` |
| Commit | [`a335957c53009d6c4f82e8176a9abf975b736979`](https://github.com/Sarthakischauhan/symphony/tree/a335957c53009d6c4f82e8176a9abf975b736979/core_server) |
| Version | `core-server` 0.4.0 |
| License | MIT (`vendor/core_server/LICENSE`, same owner) |

Rules:

- Do not edit anything under `vendor/core_server/`. It must stay byte-identical
  to the commit above so it can be swapped out wholesale.
- Application code imports it only as the published package would be
  imported (`from core_server import ...`), and depends on it by its
  distribution name `core-server`. When it is published, replace the path
  source in `apps/symphony-server/pyproject.toml` with a version pin and
  delete this directory.
- This provenance note lives here rather than in
  `vendor/core_server/README.md` because upstream already ships a README at
  that path and the copy must stay unedited.

Re-sync:

```sh
rm -rf vendor/core_server
git clone https://github.com/Sarthakischauhan/symphony /tmp/symphony
git -C /tmp/symphony checkout <sha>
cp -r /tmp/symphony/core_server vendor/core_server
```
