# dsh-cad-workbench

**Mechanical CAD workbench** for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness):
drive a local SOLIDWORKS installation through ten deterministic `cadw_*` tools.

The model never writes COM calls. It submits a declarative **Feature Graph** (units: mm);
the plugin validates it, builds it inside an undo-guarded transaction, measures the result,
renders four views, exports STEP/STL/PDF, creates a first-angle drawing, reviews DXF/STEP/STL,
and records the evidence in a job ledger.

## Tools

| Tool | Purpose |
|---|---|
| `cadw_doctor` | Environment and capability probe (Python, SolidWorks, templates, FreeCAD, lock) |
| `cadw_session` | Document session with `doc_id` identity tokens |
| `cadw_model` | Feature-graph modelling: sketch/extrude/cut/revolve/hole/fillet/chamfer; dry-run, rollback, `expect` verification |
| `cadw_measure` | Exact geometry facts, sketch audit, plane transforms |
| `cadw_capture` | Four views + 2×2 contact sheet, blank-frame detection, inline images |
| `cadw_export` | STEP/IGES/STL/3MF/PDF/PNG with bytes + sha256 |
| `cadw_drawing` | First-angle drawing with PDF/DXF/DWG export |
| `cadw_review` | Read-only DXF (7 checks) / STL (watertight) / STEP (schema, unit, heuristic bbox) review |
| `cadw_assembly` | Assembly: insert components, plane mates, fix/float, list |
| `cadw_job` | Immutable job ledger and delivery verdict |

Bundled skills: `cad-modeling-workflow`, `solidworks-com`, `cad-feature-graph`, `cad-drawing-gb`.

## Install

Host-only bundle (no build step, no MCP server):

```
plugin_manager install_bundle target=<absolute path to this repo>
```

Then restart DSH and run `cadw_doctor` in a new session.

## Safety

- Document identity tokens; writes verify path/title first.
- Cross-process SolidWorks lock (`DOC_BUSY` instead of interleaved writes).
- Precise rollback by deleting only the features this job created.
- `confirm:true` required to overwrite documents, discard unsaved changes, or close all.
- Workspace-confined paths; no network, no telemetry, no user-code execution.

## Status

Live acceptance on Windows 11 + SOLIDWORKS 2024 SP0.1: **31/31 checks pass**
(plate with through hole and fillets, measured bbox/volume, four views, STEP/STL/PDF,
A3 drawing, DXF/STEP/STL review, precise rollback, stale-token rejection, lock contention).

v0.1.1 audit regressions: **10/10** — top-face hole placement, chamfer 125 mm³, closed
polyline, partial-feature rollback, open/save. Cross-process lock is now an OS file lock;
exports no longer accept a stale pre-existing file as success.

Offline: `python test/run_all.py` — JS contract tests plus Python graph/review cases.

## Limits (v0.2)

No interference checking/motion, no FreeCAD/CadQuery backend, no shell/slot/rounded-rect,
no swept cut/loft, no web viewer, no CAD-mode preset. Unsupported requests fail loudly.

## License

MIT. Ideas and API facts are credited in [NOTICE.md](NOTICE.md); no code was copied
from upstream projects (some AGPL-3.0).