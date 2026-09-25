---
name: image-to-cad
description: Convert a flat image (PNG/JPG/sketch/drawing/blueprint/logo) into a printable parametric 3D model via the cad-agent container. Use when asked for an STL, a print file, CAD, or "make this 3D" from a picture. Covers the vision-spec-code-render-critique loop and the build123d patterns that go with it.
---

# Image → printable CAD

The container does the CAD. You do the seeing and deciding. `bbw3d` moves things
between the two.

Full operating rules live in `.claude/agents/3d-designer.md` — read that first if
you are running the loop. This skill is the reference for the parts that need
detail: reading a design image, and writing build123d that survives critique.

## Reading a design image

Work out which kind you have, because it changes what you can trust:

| What you're looking at | Trust it for | Don't trust it for |
|---|---|---|
| Dimensioned technical drawing | everything — use the numbers as given | nothing; this is the best case |
| Orthographic views (front/top/side) | proportions, feature placement, symmetry | absolute size unless dimensioned |
| Freehand sketch | intent, topology, which features exist | any dimension, straightness, symmetry |
| Photo of an object | proportions, rough profile | size, hidden geometry, anything behind the object |
| Logo / flat art | the 2D outline | depth, relief, bevel — all of that is your call |

Then, before coding, answer these explicitly:

1. **Which view is this?** A single photo of a bracket does not show the back.
   Say what you are inferring versus what you can see.
2. **Where's the scale reference?** A dimension on the drawing, a known object in
   frame, or a stated size from me. No reference → pick a size, record it as an
   assumption, move on.
3. **What's the build order?** Largest solid first, then additions, then
   subtractions (holes, pockets), then edge treatments (fillet, chamfer) last.
4. **What's symmetric?** Symmetry is a modelling shortcut and a correctness check.
5. **What's hidden?** Name it. Hidden geometry is where confident wrong models
   come from.

## Two rules the container enforces silently

### 1. No import statements. Ever.

Submitted code goes through a substring blacklist, read from `cad_engine.py`:

```python
['import ', 'eval(', 'exec(', 'os.', 'subprocess', 'open(', 'write(', 'read(', 'socket']
```

A match returns **HTTP 200** with `success: false` in the body, so a refusal can
easily read as a success. These are plain substring checks, so they catch
innocent code too: a variable named `pos` followed by a dot contains `os.`, and
any method called `read(` or `write(` trips it.

**build123d is already in the execution namespace.** Use `Box`, `BuildPart`,
`extrude` and the rest directly, with no import line at all. `bbw3d` checks your
code against the list before sending and names the offender.

### 2. Assign the finished model to `result`

`execute_code` searches the namespace for the shape you built. When it finds
none it *still returns success*, with the warning buried in `output`:

```
[Warning: No 3D shape found in result. Assign to 'result' variable.]
```

A model that "succeeded" with no geometry fails confusingly at render time. End
every submission with an explicit assignment.

## build123d that survives critique

Constants at the top. Every critique round should be a one-line change.

```python
# NO import line - the container refuses it, and build123d is already loaded.

# --- dimensions (mm) — from the sketch unless noted
WIDTH      = 70.0
DEPTH      = 80.0
HEIGHT     = 60.0
WALL       = 3.0     # ASSUMED: not dimensioned in the sketch
HOLE_D     = 5.0
LIP_H      = 8.0

with BuildPart() as part:
    Box(WIDTH, DEPTH, HEIGHT)

    # pocket, cut from the top face
    top = part.faces().sort_by(Axis.Z)[-1]
    with BuildSketch(top):
        Rectangle(WIDTH - 2 * WALL, DEPTH - 2 * WALL)
    extrude(amount=-(HEIGHT - WALL), mode=Mode.SUBTRACT)

    # through-hole
    with BuildSketch(top):
        Circle(radius=HOLE_D / 2)
    extrude(amount=-HEIGHT, mode=Mode.SUBTRACT)

    # edge treatment last
    fillet(part.edges().filter_by(Axis.Z), radius=2.0)

result = part.part          # <- the container keeps whatever lands here
```

Patterns worth knowing:

- **Faces:** `part.faces().sort_by(Axis.Z)[-1]` (topmost), `[0]` (bottom),
  `.filter_by(Plane.XY)`, `.group_by(Axis.Z)[-1]`
- **Edges:** `part.edges().filter_by(Axis.Z)`, `.group_by(Axis.Z)[-1]`,
  `.sort_by(SortBy.LENGTH)`
- **Revolve** for anything turned (bottles, knobs): sketch the profile, `revolve()`
- **Loft** between two sketches on offset planes for tapered shapes
- **Mirror** for symmetric parts: model half, `mirror(about=Plane.YZ)`
- **Locations** for arrays: `with Locations((x, y)): Circle(r)` inside a sketch
- **Text relief** for logos: `Text("BBW", font_size=10)` in a sketch, then
  `extrude(amount=1.5)` to emboss or `mode=Mode.SUBTRACT` to engrave

Failure modes to expect:

- **Non-manifold after boolean** — usually coplanar faces from a cut that exactly
  meets a surface. Overshoot the cut (`amount=-HEIGHT - 1`) instead of matching exactly.
- **Fillet fails** — radius too large for the edge, or the selection grabbed an
  edge that can't take it. Shrink it, or narrow the selection.
- **Empty result** — the sketch plane was wrong, so the extrude cut nothing. Render
  and look; don't assume it worked because it didn't error.

## Printability

`bbw3d check` before every export. What matters:

- **Manifold / watertight** — non-negotiable; a slicer will reject or silently mangle it
- **Wall thickness** — thin walls are the usual cause of a failed print. Keep
  ≥ 2 × nozzle (so ≥ 0.8mm at 0.4mm, and 1.5–2mm to be comfortable)
- **Overhangs** past ~45° need supports; say so in `print_notes` rather than
  silently leaving it
- **Flat base** — a model that needs a raft for no reason is a design flaw
- **Holes print undersize** — add ~0.2mm to a hole that has to fit a 5mm rod

## Commands

```bash
bbw3d health                                   # container up?
bbw3d new design.png                           # start a job folder
bbw3d spec                                     # gate: is the spec complete?
bbw3d create --code-file <job>/model.py
bbw3d render --kind multiview                  # then READ the PNGs
bbw3d render --kind blueprint                  # dimensioned drawing
bbw3d modify --code-file <job>/model.py --because "what the render got wrong"
bbw3d measure                                  # numbers, not vibes
bbw3d check                                    # printability
bbw3d export --format stl                      # stl | step | 3mf
bbw3d show                                     # the paper trail
```
