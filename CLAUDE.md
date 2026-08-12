# Oliver Twist — project rules for Claude

Sister project to Dream Street Shuffle, built on the same pipeline. Title is a
placeholder.

## NEVER read `Oliver Twist.html`

It is a **compiled artifact**, generated from the .twee by `sync_html.py`.
Reading it wastes tokens, and it will only grow once audio is embedded.

- **Source of truth:** `Oliver Twist.twee`
- **Compiled output:** `Oliver Twist.html` (DO NOT READ — write through .twee + sync)
- **Build script:** `sync_html.py`
- **Map generator:** `generate_map.py` → `GAME-MAP.md` + `GAME-MAP.html`

To verify compiled output, grep it — never `Read` it:

```
grep -o '<tw-passagedata[^>]*name="Fagin.s Den"[^>]*' "Oliver Twist.html"
```

## Workflow

1. Dr Quill picks a passage to work on.
2. Show the prose only. Don't quote large chunks back.
3. Edit with the `Edit` tool — never alter his creative text.
4. After a loop of edits: `python3 sync_html.py`, then `python3 generate_map.py`.
5. Check the map's Orphans / Dead ends / Broken links sections before reporting done.
6. Tell him "synced, commit when ready." **Never run git commands.**

## Critical rules

- **NO git commands.** Dr Quill handles all commits via GitHub Desktop.
- **Don't alter his creative text.**
- **Be token-efficient.** Targeted Greps + partial Reads.
- **Batch notes.** Two or more items in one message means a task list first.

## Stub convention

Every unwritten passage is wrapped in `<span class="stub">`, which renders as an
obvious pink block. A passage is finished when its stub wrapper is gone. Never
add prose to a stub — replace the whole wrapper, or leave it alone.

**Structural notes must not contain live Harlowe syntax.** A bare `$variable` or
`(if: ...)` inside stub prose gets executed by the engine. Wrap any such mention
in backticks. This has already broken the build once.

## Structure

```
PROLOGUE (linear, sets state)  →  HUB A: Fagin's den, nights 1–3
  →  INTERLUDE: Pentonville (breaks the hub)  →  HUB B: nights 4–7
  →  THE DOOR: Chertsey (one-way)  →  EPILOGUE (branch → 4 endings)
```

Two axes, pulling against each other: `$innocence` and `$standing`. Nothing
raises both. Excursions and endings gate on them, so the mechanic makes the
argument rather than the prose. `$night` counts to `$nightsTotal` (currently 7);
fixed nights are Fagin's, open nights are the player's.

Key rulings (Dr Quill, 2026-08-12):
- **Nancy's room is never locked.** What she gives is gated instead: she only
  opens up (`$nancyOpened`) at innocence 55+. A corrupt player gets a guarded
  scene and no flag.
- **Nancy is saveable**, but it takes both halves: her trust from the hub
  (`$nancyOpened`) AND the warning at London Bridge. Either alone fails.
- **The last choice is Fagin.** At Monks, testifying (`$gaveUpFagin`) routes to
  the Rope even on a Locket-qualified run.
- Ending restart links must clear sessionStorage's `Saved Session` key before
  reloading — bare `(restart:)` gets rehydrated by Harlowe's session restore
  and the old run's state survives.

## Reusable from Dream Street Shuffle

Not yet carried over, available when wanted: `window.dssAudio` (procedural SFX +
music player), `window.dssScene` (3D atmosphere toolkit — procedural materials,
Victorian gas-lamp), the minigame harness, the popup serializer. See the DSS
memory notes before re-solving anything in those areas.
