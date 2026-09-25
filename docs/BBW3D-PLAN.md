# BBW3D — Image → 3D Model Spin-Off

**Status:** Plan only. No code written yet.
**Date:** 2026-09-25
**Scope:** A standalone, lightweight project that takes a flat image (PNG / JPG / WEBP / SVG) and produces a 3D model. Deliberately **not** embedded in core BBW — separate repo, separate deploy, separate deps.

---

## 1. What `cad-agent` actually is (review)

Repo: https://github.com/Svetlana-DAO-LLC/cad-agent (default branch `master`, Python, © Svetlana DAO LLC 2026)

**One-line summary:** it gives an AI agent *eyes* for CAD. It is a Dockerised rendering + modeling server. The agent writes parametric Python, the container executes it, renders it to PNG, and hands the picture back so the agent can look at its own work and fix it.

### Architecture
```
   your agent  ──HTTP :8123 (or MCP stdio)──▶  cad-agent container
   (writes code,                               build123d  → geometry
    looks at PNGs,                             VTK        → 3D renders
    decides next step)                         matplotlib → 2D blueprints
                                               exporters  → STL / STEP / 3MF
          ◀──────── PNG images + JSON metadata ────────
```
Hard rule from its own docs: *"Never do STL manipulation, mesh processing, or rendering outside the container."* All CAD logic is server-side; the agent only sends commands and reads images.

### Surface it exposes
| Endpoint | Purpose |
|---|---|
| `GET /health` | health check |
| `POST /model/create` | run build123d code → a named model |
| `POST /model/modify` | apply a delta to an existing model |
| `GET /model/list` | list session models |
| `GET /model/{name}/measure` | bounding box + dimensions |
| `POST /render/3d` | shaded 3D render (view angle param) |
| `POST /render/2d` | single orthographic view |
| `POST /render/blueprint` | full 2D technical drawing |
| `POST /render/multiview` | 4-view composite |
| `POST /export` | STL / STEP / 3MF |
| `POST /analyze/printability` | manifold / watertight check |
| `POST /ai/feedback` | feed critique + code back into the loop |

Top level: `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `requirements.txt`, `SKILL.md`, `src/`, `examples/`, `tests/`, `.githooks/`, `check_api.py`. Runs as an HTTP service *or* as an MCP server (`docker run -i --rm cad-agent:latest mcp`). `SKILL.md` means it also drops in as a Claude Code skill.

### The critical finding
**`cad-agent` does not accept images as input.** Images are only ever an *output*. It has no image→mesh capability anywhere in it.

So it does **not** do what you asked for on its own. What it *does* give us is the other half of the problem: a tight **look-at-it-and-fix-it loop** that turns a sloppy first attempt into a clean, measurable, printable model. That is genuinely valuable — it's the difference between "a blob that looks like the drawing" and "a part that is 42.0 mm wide and watertight."

### Licensing — read this before we ship anything paid
- **PolyForm Small Business License 1.0.0 + Perimeter.**
- Free for us *today*: under 100 employees and under $1M revenue. Internal use is fine.
- **Perimeter clause** restricts use in a *competing* product. If BBW3D becomes a public/paid service whose value is essentially "CAD generation as a service," we are in grey-to-bad territory and need either a commercial license from them or our own wrapper.
- Mitigation is cheap: the engine underneath (`build123d`) is Apache-2.0, as are VTK and matplotlib. The cad-agent value-add is the render-loop wrapper — a few hundred lines we can write ourselves if licensing gets in the way. **Plan: use cad-agent as-is for internal prototyping; keep a clean-room `bbw3d-cad` fallback in the design so we're never locked.**

### Honesty note on this review
I read the README, `SKILL.md`, and the repo's file listing. I could **not** read `src/` — unauthenticated GitHub API returned 403 and that repo is outside this session's allowed repo scope. So endpoint *names and shapes* are from their docs, not verified against code. First implementation step below is a 30-minute spike that verifies the real API before we build on it.

---

## 2. What BBW3D should be

Two engines, one router. Image→3D is not one problem, it's two, and they need different tools.

### Lane A — Generative mesh (fast, organic, pretty)
**Input:** any PNG/JPG. **Output:** GLB mesh with baked texture. **Time:** ~1–3 min. **Infra:** none, API call.

We already have this wired in this environment: the **Highfield connector's `generate_3d`** turns an image into a 3D GLB mesh. Also available: `scene_builder_3d_*` (a real Blender backend — import asset, run Python, export GLB/.blend) for cleanup, scaling, staging and rendering turnarounds.

Good for: characters, creatures, props, book-cover art in 3D, merch mockups, AR/web viewers, spin-around video.
Bad for: anything that has to be dimensionally correct or 3D-printed cleanly. Gen-AI meshes are typically non-manifold, hollow-ish, weird topology.

### Lane B — Parametric CAD loop (precise, printable) ← this is where cad-agent fits
**Input:** PNG of a drawing, sketch, blueprint, logo, orthographic view, product photo. **Output:** STL / STEP / 3MF + blueprint PNGs + measurements. **Time:** ~2–10 min of iteration. **Infra:** the container.

```
image ──▶ VISION PASS          Claude looks at the image, writes a spec:
          (no CAD yet)          primitives, dimensions, symmetry, holes, fillets,
                               units, what's ambiguous
            │
            ▼
      build123d code ──▶ POST /model/create ──▶ POST /render/multiview
            ▲                                          │
            │                                          ▼
            └──── CRITIQUE PASS ◀──── Claude compares render vs. original image
                  (what's wrong?)      "chamfer is on the wrong edge, base is 3mm too thin"
                       │
                  POST /model/modify          ...loop until it matches (cap: 5 rounds)
                       │
                       ▼
            /analyze/printability ──▶ /export (STL/STEP/3MF)
```
This is exactly the loop cad-agent was built for. We supply the missing front end: **image → spec → code**.

### The router
Classify the uploaded image first, then pick a lane:
- Line art, technical drawing, orthographic views, dimensioned sketch, logo/text to emboss, flat geometric shape → **Lane B**
- Photo of a person/creature/organic object, painted illustration, character art → **Lane A**
- Product photo of a manufactured object (a bottle, a case, a bracket) → **Lane A for the blockout, Lane B for the real part**; offer both, label them "visual" vs "buildable"
- Ambiguous → ask one question: *"Do you want this for looks (web/AR/video) or for making (3D print/CNC)?"* One question, not a quiz.

---

## 3. Repo and shape

**New repo: `ZidPix/bbw3d`.** Not a folder in core BBW, not a shared dep. Zero imports between them. If they ever need to talk, it's over HTTP with a signed URL — nothing tighter.

```
bbw3d/
├── .claude/
│   ├── agents/
│   │   └── 3d-designer.md          # the agent (see §4)
│   └── skills/
│       ├── image-to-cad/SKILL.md   # Lane B: vision→build123d→iterate
│       └── image-to-mesh/SKILL.md  # Lane A: gen-3D→cleanup→export
├── src/bbw3d/
│   ├── router.py                   # classify image → lane
│   ├── vision.py                   # image → design spec (JSON)
│   ├── cad_client.py               # thin client for cad-agent HTTP API
│   ├── mesh_client.py              # Highfield generate_3d wrapper
│   ├── critique.py                 # render vs. source-image compare loop
│   └── pipeline.py                 # orchestration + retry caps + budget guard
├── cli.py                          #  bbw3d build ./design.png --mode print
├── api.py                          # optional FastAPI: POST /build (multipart)
├── docker-compose.yml              # our service + cad-agent, one `up`
├── out/                            # gitignored — models never committed
└── docs/
```

**Stack:** Python 3.11, FastAPI + Uvicorn, httpx, Pillow, Pydantic, Typer for the CLI, pytest. Plus the cad-agent container as a sidecar. That's it — this is a thin orchestrator by design; the heavy lifting is in the container and the APIs.

**Deliberately excluded to keep it light:** no database (filesystem + JSON manifest per job), no auth system at first (local CLI), no queue (synchronous, one job at a time), no frontend until the CLI proves the pipeline.

### Infrastructure reality check
- The cad-agent image is heavy (build123d/OCP + VTK ≈ 2–3 GB). **It will not run in a serverless function.** It needs a container host: local Docker for dev, then Fly.io / Railway / a small always-on VM (2 vCPU, 4 GB) when we want it remote.
- **This cloud session has the Docker CLI but no daemon**, so I cannot build or run the container from here. Two options when we implement: (a) you run `docker compose up` on your machine and I drive it, or (b) we `pip install build123d` directly in the session and run the geometry in-process — slower to set up, no VTK renders, but enough to validate the code-generation half. Recommend (a).
- Lane A needs no infra at all, which is why it's Milestone 1.

---

## 4. The 3D Designer agent

A `.claude/agents/3d-designer.md` subagent, defined once, then invoked with an image. Draft definition:

> **name:** 3d-designer
> **description:** Turns a reference image into a 3D model. Use when given a PNG/JPG/sketch/blueprint/logo and asked for a 3D model, STL, print file, or GLB.
> **tools:** Read, Write, Bash, Glob, Grep, Highfield `generate_3d` + `scene_builder_3d_*`, cad-agent MCP (or the HTTP client)
>
> **Operating rules**
> 1. **Look at the image first.** Read it. Describe what you see in the spec before writing a single line of CAD. Never generate geometry from the filename or from my prose alone.
> 2. **Write a spec, then code.** The spec names primitives, dimensions, units, symmetry, features, and explicitly lists what the image does not tell you.
> 3. **State assumptions, don't stall.** If overall height is unknown, pick a sane value, write it down as an assumption, and keep going. One clarifying question maximum, and only when the whole result hinges on it.
> 4. **Never trust unrendered geometry.** After every create/modify, render and *look*. Compare against the source image. Name what's wrong before fixing it.
> 5. **All CAD stays in the container.** No mesh/STL manipulation outside it.
> 6. **Cap the loop at 5 iterations.** If it isn't converging by 5, stop and report what's blocking with the best render attached — don't burn cycles silently.
> 7. **Check printability before declaring done** for Lane B. Manifold + watertight + wall thickness, or say plainly that it isn't printable.
> 8. **Never commit output.** `out/` is gitignored; models are artifacts, not source.
> 9. **Report honestly.** "Matches the drawing except the fillet radius, which I estimated at 2 mm" beats "Done!"

---

## 5. Milestones

| # | Milestone | What ships | Effort |
|---|---|---|---|
| 0 | **Spike / verify** | Stand up cad-agent locally, hit every endpoint, confirm request/response shapes against my §1 table, one hello-world box → render → STL. Correct this plan where reality differs. | ~1 session |
| 1 | **Lane A end-to-end** | `bbw3d mesh ./art.png` → GLB + turnaround PNG via Highfield. No container needed, so this is the fastest thing that works. | ~1 session |
| 2 | **Lane B, single shot** | Vision pass → build123d code → create → multiview render → export STL. No critique loop yet. | ~1–2 sessions |
| 3 | **The critique loop** | Render-vs-image compare, `/model/modify`, iteration cap, printability gate. This is the heart of it. | ~2 sessions |
| 4 | **Router + CLI polish** | Auto-lane selection, `--mode print\|visual\|both`, per-job manifest with spec + assumptions + iteration log. | ~1 session |
| 5 | **The agent** | `.claude/agents/3d-designer.md` + the two skills, so "here's a design, build it" just works. | ~1 session |
| 6 | *(optional)* **Service** | FastAPI `POST /build`, deployed container, signed-URL handoff if core BBW ever needs to call it. | ~2 sessions |

**Suggested order:** 0 → 1 → 2 → 3 → 5 → 4 → 6. Milestone 1 gives you something usable on day one; Milestone 3 is where it gets *good*.

---

## 6. Decisions I need from you

1. **New repo `ZidPix/bbw3d`, or keep building in `SpicyBookclub-`?** (This repo is currently empty — no commits at all, so either is clean. I recommend the separate repo, per your "keep it light.")
2. **cad-agent as-is, or clean-room wrapper?** As-is is faster and free for internal use; clean-room protects us if BBW3D goes paid/public. Recommend: as-is now, revisit before any launch.
3. **Primary output target** — printable STL (drives Lane B priority) or pretty GLB for web/AR (drives Lane A)? Both are in the plan; this sets the order.
4. **Where does the container live** — your machine only, or a small hosted VM?

---

## 7. What I'm honestly unsure about

- **`src/` unverified.** Endpoint shapes come from their docs. Milestone 0 exists to check them.
- **Vision→dimensions is the hard part.** A photo with no dimensions gives proportions, not millimetres. Expect to hand it a scale reference or a stated dimension for print work. Sketches with numbers on them work far better than photos.
- **Convergence isn't guaranteed.** The critique loop is excellent at "the hole is in the wrong place" and weak at "this whole approach to the geometry is wrong." When the first spec is bad, iterating won't rescue it — the fix is a better spec, which is why the vision pass is a separate, deliberate step.
- **Gen-AI meshes are not print-ready.** Lane A output will need real repair work (remesh, hollow, thicken) before anyone prints it. Blender via `scene_builder_3d_run_python` can do a lot of that, but don't promise print quality out of Lane A.
