# BBW3D

Give it a design image, get back a printable 3D model. Standalone spin-off —
nothing is shared with core BBW.

**The agent is the brain, Python is the hands.** Claude reads the design image,
writes a dimensioned spec, generates build123d code, then looks at the render
next to the original and names what is wrong before fixing it. The `bbw3d` CLI
only carries code into the container and pictures back out. No LLM API key, no
prompt plumbing.

- Operating rules for the loop: `.claude/agents/3d-designer.md`
- Reading design images + build123d patterns: `.claude/skills/image-to-cad/SKILL.md`
- Background and decisions: `docs/BBW3D-PLAN.md`

## Run it

```powershell
.\scripts\setup.ps1           # prerequisites, patches, build, up, verify
.\scripts\setup.ps1 -Diagnose # logs + sandbox inspection + verify, no rebuild
python -m unittest discover -s tests   # 92 tests, no container needed
```

Container on localhost:8123, source at `../cad-agent`, output under `out/<job>/`.

## cad-agent is patched, not used as shipped

`scripts/patch_cad_agent.py` applies fixes to the upstream checkout on every
setup; `scripts/repair_cad_agent.py` fixes escaped-quote SyntaxErrors in its
published `.py` files. Both are idempotent and only act when the defect is
present. Treat that checkout as a fork we maintain.

Defects found so far, all diagnosed from a running container:

1. **`_build_namespace` could never succeed** — it ran an import statement
   inside a `__builtins__` dict with no `__import__`, so *every* request died
   with `build123d not available: __import__ not found`. Patched to import
   build123d outside the sandbox and copy its public names in. Do **not**
   "simplify" this by adding `__import__` to their allowlist: that hands
   submitted code an escape hatch, since `__import__("os")` sidesteps their
   `"os."` blacklist entry.
2. **Silent failure warning discarded** — their `finally` block overwrote
   `result["output"]`, destroying the message explaining why a create stored
   nothing. Patched to append.
3. **`pyglet` missing, then unpinned** — 3D/multiview renders need
   `trimesh.viewer.windowed`, which uses the pyglet 1.x API. Pinned `pyglet<2`.
4. **Every 3D render was an extreme close-up of one face** —
   `_render_3d_trimesh` ignored its own `view` argument and called
   `mesh.scene().save_image()` with trimesh's default camera, which looks
   straight down -Y from so close that the near face overflows the frustum. The
   PNG came back as one flat grey rectangle: HTTP 200, valid image, *looks*
   exactly like a failed render. It was not — the geometry was drawn correctly
   the whole time. Patched to aim the camera along `VIEW_DIRECTIONS[view]` and
   fit the bounding corners with `camera.look_at`, plus a 12% margin.

   Note the fallback chain in `render_3d`: VTK, then pyrender, then trimesh.
   Neither `vtk` nor `pyrender` is in `requirements.txt`, so **only the trimesh
   path ever runs** — the logs say `VTK failed (No module named 'vtk')` on every
   single render. Do not chase that message; it is the normal path. Installing
   pyrender is a dead end: it needs their patched PyOpenGL fork
   (`OSMesaCreateContextAttribs` is absent from the released wheels).

## The API contract, as verified (not as documented)

Their README and SKILL.md are wrong in several places. These facts come from a
live container; `src/bbw3d/cad_client.py` is the single place they are encoded.

| Fact | Detail |
|---|---|
| Model name key | **`name`**. `model_name` is silently ignored — the server substitutes its own default, so a wrong key looks like a missing model. |
| Code dialect | `with BuildPart() as part:` … then **`result = part.part`** |
| No imports | build123d is pre-loaded in the namespace |
| Blacklist | `import `, `eval(`, `exec(`, `os.`, `subprocess`, `open(`, `write(`, `read(`, `socket` — **plain substring checks**, so a variable named `pos` followed by a dot trips `os.` |
| Refusal | HTTP **200** with `success: false` — never trust the status code alone |
| Silent failure | `success: true` + `geometry: null` means **nothing was stored** |
| `/render/blueprint` | **404** — documented but absent from this build |
| `/render/2d` | works (matplotlib); returns `base64`, `path` and `view` |
| `/render/3d`, `/render/multiview` | Work. Need `pyglet<2` **and** the camera patch below |
| Blank-looking render | A flat one-colour PNG is **not** a GL failure — check the framing before the renderer |
| Export | STL and STEP good. **3MF is suspect** — same byte count as the STL, so their exporter is probably writing STL content |

Probe box (30×20×10) verified: volume 6000 mm³, surface 2200 mm², 6 faces,
12 edges, 8 vertices, watertight, min wall 0.8 mm.

## Windows gotchas already paid for

- **Windows PowerShell 5.1**: `&&` is a syntax error; use `;` or separate lines.
- **`scripts/*.ps1` must be pure ASCII with a UTF-8 BOM.** PS 5.1 decodes as
  cp1252 without one, and a UTF-8 em dash then ends in `"` (U+201D), which it
  treats as a string delimiter. Enforced by `tests/test_powershell_encoding.py`.
- **Under `$ErrorActionPreference = 'Stop'`, `2>&1` makes any stderr output a
  terminating error** — even from a command that succeeded. Use `Invoke-Native`.
- **Shell scripts must keep LF endings.** A CRLF shebang makes the kernel hunt
  for `/bin/sh\r`, reported as `no such file or directory`. `.gitattributes`
  pins this; cad-agent is cloned with `core.autocrlf=false`.
- `docker.exe` may be absent from a shell's PATH even when Docker Desktop is
  running — PATH is snapshotted at process start. `Resolve-Docker` handles it.
  It also handles the **per-user install layout**,
  `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`, which is where the
  "install for me only" build puts it. Finding `docker.exe` is not enough:
  its directory must go **on PATH**, because the CLI shells out to siblings by
  bare name and a build otherwise dies with
  `error getting credentials - exec: "docker-credential-desktop": executable
  file not found in %PATH%`.
- The `2>&1` rule applies to **how you invoke `setup.ps1`**, not just to code
  inside it. Piping the script through `Tee-Object` with `*>&1` re-creates the
  trap from outside: Docker's ordinary build progress on stderr becomes a
  `NativeCommandError` and the run dies at the first build line. Call the script
  plainly and let it write to the console.

## House rules

- Endpoint paths and payload keys live **only** in `src/bbw3d/cad_client.py`.
- Zero runtime dependencies. Standard library only; the container owns
  everything heavy. Do not add a package without a real reason.
- Generated models are artifacts, never source. `out/` is gitignored.
- Every finding about the container's behaviour gets a test, driven by the
  observed response — see `tests/test_live_behaviour.py`.

## Where things stand

Working end to end: create → measure → printability → export (STL/STEP), 2D
renders, **and 3D + multiview renders** — verified by eye, not just by exit
code: the probe box comes back as a shaded iso solid, framed with a margin.

The GL stack was never the problem. For the record, in case a render looks
wrong again: Xvfb is running (`/tmp/.X11-unix/X99`), Mesa gives GL 4.5 via
llvmpipe, the depth buffer is 32-bit, and pyglet vertex lists draw correctly.
The way to tell a framing bug from a render bug is to read the **depth buffer**
where the image looks blank — if it is not 1.0, the geometry was drawn and the
camera is what is wrong.

Known gap, pre-existing and unrelated to rendering: **6 tests fail**
(`test_cad_client.TestExtractAssets` ×4, `test_repair_cad_agent.TestRepairTree`
×2). `extract_assets` returns `[]` where the test expects the resolved
`/renders` and `/workspace` mount paths. The base64 render path is unaffected,
which is why verify passes. Diagnose before trusting on-disk asset extraction.

Next: a first real design image through the full loop.
