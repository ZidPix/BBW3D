# BBW3D

Give it a design, get back a printable 3D model.

A flat image goes in (PNG, JPG, sketch, drawing, logo). An STL comes out, by way
of a loop that renders the model and *looks* at it against your original before
calling it done.

Standalone by design — no shared code, no shared deps, nothing imported from core
BBW. Stdlib-only Python, so the whole thing installs in seconds.

## How it works

```
   design.png
       │
       ▼
   ┌─────────────────────────────────────────────────┐
   │  3D Designer agent  (the eyes and the judgement)│
   │   looks at the image → writes a dimensioned     │
   │   spec → writes build123d code → looks at the   │
   │   render → names what's wrong → fixes it        │
   └───────────────┬─────────────────────────────────┘
                   │  bbw3d CLI (the hands)
                   ▼
   ┌─────────────────────────────────────────────────┐
   │  cad-agent container  (all the CAD)             │
   │   build123d geometry · VTK renders ·            │
   │   blueprints · printability · STL/STEP/3MF      │
   └─────────────────────────────────────────────────┘
                   │
                   ▼
   out/<job>/  renders · spec.json · code per round · exports · manifest
```

The container never makes decisions and the agent never touches a mesh. This CLI
just carries code in and pictures out.

## Setup (local machine, needs Docker)

```bash
# 1. the CAD container — built from the cad-agent repo, not published
git clone https://github.com/Svetlana-DAO-LLC/cad-agent ../cad-agent
docker build -t cad-agent:latest ../cad-agent
docker compose up -d

# 2. this toolbelt
pip install -e .

# 3. confirm the two can talk
bbw3d health

# 4. confirm the container behaves the way this client expects
bbw3d verify
```

**Run `bbw3d verify` before trusting anything.** The endpoint paths and payload
keys in `src/bbw3d/cad_client.py` were written from cad-agent's README and
SKILL.md, not from its source. `verify` probes the whole surface with a known
30×20×10 box and reports what actually came back. Anything that differs gets
fixed in `cad_client.py` — the one place those names are defined.

## Use it

Hand the agent a design:

> Build me this. ./designs/phone-stand.png

The `3d-designer` agent takes it from there. Or drive it by hand:

```bash
bbw3d new designs/phone-stand.png     # job folder, source image copied
# write <job>/spec.json  — dimensions, features, assumptions
bbw3d spec                            # refuses to pass an incomplete spec
# write <job>/model.py   — build123d
bbw3d create --code-file <job>/model.py
bbw3d render --kind multiview         # then LOOK at the PNGs
bbw3d modify --code-file <job>/model.py --because "base 3mm too thin"
bbw3d measure                         # numbers, not vibes
bbw3d check                           # manifold / watertight
bbw3d export --format stl
bbw3d show                            # the paper trail
```

`--job` defaults to the most recent job under `out/`.

## Configuration

| Env var | Default | What it does |
|---|---|---|
| `BBW3D_CAD_URL` | `http://localhost:8123` | where cad-agent is listening |
| `BBW3D_OUT` | `out` | where job folders are written |
| `BBW3D_WORKSPACE` | `workspace` | host side of the container's `/workspace` mount |
| `BBW3D_MAX_ITERATIONS` | `5` | critique rounds before the designer must stop and report |
| `BBW3D_TIMEOUT` | `180` | HTTP timeout in seconds |
| `BBW3D_UNITS` | `mm` | default spec units |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No container, no network, no pip. They cover the response-shape handling (raw
PNG bytes, base64 anywhere in the JSON, `data:` URIs, workspace file paths), the
endpoint contract, and the job/spec bookkeeping.

## What this is not

- **Not a generative mesh tool.** It builds parametric CAD, so output is clean,
  measurable and printable. If you want a pretty GLB of a character for web or AR,
  that's the other lane — see `docs/BBW3D-PLAN.md`.
- **Not a photogrammetry tool.** One flat image, not a scan.
- **Not magic about dimensions.** A photo gives proportions, not millimetres. For
  print-accurate work, give it a dimensioned drawing or state a known size.

## Licensing note

The `cad-agent` container is **PolyForm Small Business 1.0.0 + Perimeter** (©
Svetlana DAO LLC). Free for us internally at our size. Before BBW3D becomes
anything public or paid, re-read that licence — the Perimeter clause restricts
competing products. `build123d` underneath is Apache-2.0, which is our fallback
if it ever comes to that. Details in `docs/BBW3D-PLAN.md`.

This project's own code has no licence file yet — decide one before any release.
