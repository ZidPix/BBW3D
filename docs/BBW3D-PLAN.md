# BBW3D — Image → 3D Model Spin-Off

**Status:** Decisions locked, scaffold built. Milestone 0 (verify against a live container) is next and must run on your machine.
**Date:** 2026-09-25

---

## 0. Decisions (locked 2026-09-25)

| Question | Decision |
|---|---|
| Where does it live? | **This repo**, renamed `SpicyBookclub-` → **`BBW3D`**. It was empty, so nothing is lost. |
| Primary output | **Printable STL.** Lane B (parametric CAD) is the priority; the generative GLB lane is deferred. |
| Where does the container run? | **Your machine only.** No hosted VM, no deploy target, nothing to pay for. |
| cad-agent as-is, or clean-room? | **As-is for now** — free at our size, and Lane B is worthless without its render loop. Revisit before anything public or paid. Flagging rather than blocking, since you didn't call this one; say the word if you want the clean-room path instead. |

**Renaming the repo** (GitHub → Settings → General → Repository name). Existing
clones then need `git remote set-url origin https://github.com/ZidPix/BBW3D`.
GitHub redirects the old URL, so nothing breaks immediately. I have not renamed
it myself — that's your account, and it would cut this session's own remote.

### What that reordering means
Printable-STL-first flips the milestone order: **Lane B goes first, the generative
GLB lane goes last.** Everything below reflects that.
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

**This repo, renamed `BBW3D`.** Not a folder in core BBW, not a shared dep. Zero imports between them. If they ever need to talk, it's over HTTP — nothing tighter.

This is what got built (✅) and what is still notional (○):

```
BBW3D/
├── .claude/
│   ├── agents/3d-designer.md        ✅ the designer: rules, loop, stop conditions
│   └── skills/image-to-cad/         ✅ reading a design image + build123d patterns
├── src/bbw3d/
│   ├── _http.py                     ✅ stdlib HTTP, errors that say what to do
│   ├── config.py                    ✅ env-var config
│   ├── cad_client.py                ✅ the cad-agent client (single source of endpoint truth)
│   ├── spec.py                      ✅ the design spec + its completeness gate
│   ├── job.py                       ✅ job folders, rounds, event log
│   ├── verify.py                    ✅ Milestone 0 endpoint probe
│   ├── cli.py                       ✅ the whole command surface
│   └── mesh_client.py               ○  Lane A (deferred — Highfield generate_3d)
├── tests/                           ✅ 44 tests, no container or network needed
├── docker-compose.yml               ✅ the cad-agent sidecar
├── pyproject.toml                   ✅ zero runtime deps
├── out/                             gitignored — models are artifacts, not source
└── docs/BBW3D-PLAN.md               this file
```

**Stack: Python 3.11 standard library. That's the whole list.** No FastAPI, no
httpx, no Pydantic, no Typer — `urllib`, `argparse`, `dataclasses` and `json`
cover every job here, and the container owns everything heavy. So `pip install -e .`
pulls nothing, there is no dependency tree to conflict with core BBW, and the
tests run on a bare Python. That is what "keep it light" buys us.

**Deliberately excluded:** no database (a JSON manifest per job folder), no auth,
no queue (one job at a time), no web frontend, no API server until the CLI proves
the pipeline is worth wrapping.

### Design decisions worth knowing
- **The agent is the brain; Python is the hands.** The vision pass and critique
  pass are done by Claude looking at files, not by our code calling an LLM. So
  there is no API key, no token budget, and no prompt plumbing to maintain.
  Unattended mode stays available later as Milestone 6.
- **Endpoint names live in exactly one place** (`cad_client.py`). They were taken
  from cad-agent's docs, not its source, so `bbw3d verify` exists to catch the
  difference and there is a single file to correct.
- **The client tolerates four response shapes** — raw PNG bytes, base64 anywhere
  in the JSON, `data:image/png;base64,` URIs, and `/workspace/...` file paths
  resolved through the bind mount. I don't know which one cad-agent actually
  uses, so it handles all four and tests all four.
- **Renders land on disk as PNG files.** That's how the visual loop actually
  closes in Claude Code: the agent `Read`s the file. Base64 in a response body is
  useless to it, and echoing blobs into the terminal just burns context.
- **Every round is on the record** — code per round, renders labelled by round,
  and a `--because` line saying what the previous render got wrong. When a model
  comes out wrong you can see which round broke it.

### Infrastructure reality check
- The cad-agent image is heavy (build123d/OCP + VTK ≈ 2–3 GB) and **cannot run
  serverless.** Your machine, per your call. Nothing to host, nothing to pay for.
- **This cloud session has the Docker CLI but no daemon,** so I could not build or
  run the container from here. What I did instead: wrote a fake cad-agent that
  replies in all four shapes and drove the entire CLI loop against it
  (create → render → modify → measure → check → export → show), which is how the
  two bugs mentioned in §7 were found. The real container is Milestone 0, on you.

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

Reordered for printable-STL-first.

| # | Milestone | What ships | State |
|---|---|---|---|
| 1 | **Toolbelt + agent** | `bbw3d` CLI (stdlib only), cad-agent client that survives any of the four plausible response shapes, job folders with a full paper trail, spec gate, the `3d-designer` agent and `image-to-cad` skill, 44 tests. | **✅ done** |
| 0 | **Verify against the real container** | `bbw3d verify` probes all 11 endpoints with a known 30×20×10 box and reports what actually came back. Any mismatch gets fixed in `cad_client.py`. **Needs Docker — your machine.** | ⏳ next, blocked on you |
| 2 | **First real part, end to end** | A genuine design image → spec → build123d → render → STL that slices. Shakes out the build123d patterns that actually work. | after 0 |
| 3 | **Tighten the critique loop** | Whatever milestone 2 reveals: better render framing, dimension callouts, spec fields that turned out to matter, common failure recipes into the skill. | after 2 |
| 4 | **Convenience** | `bbw3d build <image>` as a one-shot wrapper, job compare (`round 1 vs round 4`), a small library of reusable parametric patterns. | later |
| 5 | **Generative GLB lane** | `bbw3d mesh ./art.png` → GLB via Highfield `generate_3d`, Blender cleanup, turnaround render. Deferred — you chose printable first. | deferred |
| 6 | *(optional)* **Unattended mode** | Pipeline calls the Claude API itself so `bbw3d build x.png` runs with no agent in the loop. Needs an API key and a budget guard. | optional |

Milestone 0 is small — maybe twenty minutes once the container builds — but
nothing after it is trustworthy until it's done.

---

## 6. What you need to do next

1. **Rename the repo** on GitHub: Settings → General → `SpicyBookclub-` → `BBW3D`.
2. **Build the container** (one time, a few minutes):
   ```bash
   git clone https://github.com/Svetlana-DAO-LLC/cad-agent ../cad-agent
   docker build -t cad-agent:latest ../cad-agent
   docker compose up -d
   ```
3. **Run the two checks**, and send me the output of the second:
   ```bash
   pip install -e . && bbw3d health
   bbw3d verify
   ```
   `verify` *is* Milestone 0. Its report tells me exactly which of my assumptions
   about cad-agent's API were wrong, and they all live in one file.
4. **Pick a first real design** — ideally a sketch or drawing with **at least one
   dimension written on it**. That pins the scale, and it makes the difference
   between a model that fits and a model that merely looks right.

---

## 7. What I'm honestly unsure about

- **cad-agent's `src/` is still unread.** Endpoint paths and payload keys come from
  its README and SKILL.md. Unauthenticated GitHub API gave me 403 and that repo was
  outside this session's allowed scope, so `bbw3d verify` is how we find out. The
  client is written defensively for exactly this reason.
- **Whether the container returns image bytes, base64, or file paths.** Unknown, so
  all three are handled and tested. If it turns out to be something stranger,
  `extract_assets` is the one function to change.
- **Vision→dimensions is the hard part.** A photo with no dimensions gives proportions, not millimetres. Expect to hand it a scale reference or a stated dimension for print work. Sketches with numbers on them work far better than photos.
- **Convergence isn't guaranteed.** The critique loop is excellent at "the hole is in the wrong place" and weak at "this whole approach to the geometry is wrong." When the first spec is bad, iterating won't rescue it — the fix is a better spec, which is why the vision pass is a separate, deliberate step.
- **Gen-AI meshes are not print-ready.** Lane A output will need real repair work (remesh, hollow, thicken) before anyone prints it. Blender via `scene_builder_3d_run_python` can do a lot of that, but don't promise print quality out of Lane A.

### What was actually verified, and what wasn't

Verified here, no container needed:
- 44 tests pass (`python3 -m unittest discover -s tests`), covering all four
  response shapes, the endpoint contract, error surfacing, and job/spec bookkeeping.
- The full CLI loop run against a fake cad-agent that answers in every shape:
  `health → new → spec → create → render (×4 kinds) → modify → measure → check →
  export → show`, plus `verify` itself.
- A deliberately broken code submission returns the container's own error
  (`HTTP 500: NameError…`) rather than a generic failure.
- The probe correctly flagged the fake server's wrong dimensions
  (`expected_30_20_10_found: []`) — which is the exact mismatch detection
  Milestone 0 relies on.

Two real bugs that testing caught: the CLI was echoing whole base64 PNGs into its
output (context bloat for the agent, now elided), and render filenames stuttered
(`r02-multiview-views-iso.png`, now `r02-multiview-iso.png`).

**Not verified:** anything involving the real container. No build123d code in this
repo has ever been executed — including the probe box in `verify.py`, which is
only checked for valid Python syntax. Until you run `bbw3d verify`, treat the
endpoint contract as an educated guess with tests around it.
