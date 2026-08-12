#!/usr/bin/env python3
"""
generate_map.py — writer's-eye-view map of Oliver Twist.

Reads `Oliver Twist.twee` and writes:
  • GAME-MAP.md   — a structured, greppable index of every passage:
                    tags, prose preview, outgoing links (with choice text),
                    incoming links, returns (bidirectional edges), dynamic
                    links, (display:) includes. Plus global sections for
                    orphans, dead-ends, hubs, and strongly-connected
                    components (the loops that hold the game together).
  • GAME-MAP.html — interactive vis-network graph. Pan, zoom, click a
                    node to see its details. Nodes coloured by primary tag.

Re-run whenever the .twee changes. Both files are regenerated from
scratch; you never edit them by hand.

Usage:  python3 generate_map.py
"""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
TWEE = ROOT / "Oliver Twist.twee"
OUT_MD = ROOT / "GAME-MAP.md"
OUT_HTML = ROOT / "GAME-MAP.html"

# Passages we never treat as story nodes (Twine engine + assets).
SYSTEM_NAMES = {"StoryTitle", "StoryData", "StoryInit", "UserScript", "UserStylesheet"}
SYSTEM_TAGS = {"script", "stylesheet", "init", "header"}

# ──────────────────────────────────────────────────────────────────────
# 1.  Parse the .twee into passage records
# ──────────────────────────────────────────────────────────────────────

PASSAGE_HEADER_RE = re.compile(
    r'^::\s+'                              # the :: marker
    r'(?P<name>[^\[\{\n]+?)'               # name (stops at [ or { or newline)
    r'(?:\s+\[(?P<tags>[^\]]*)\])?'        # optional [tags]
    r'(?:\s+(?P<meta>\{[^\}]*\}))?'        # optional {position/size json}
    r'\s*$'
)

def parse_passages(text):
    passages = []
    current = None
    for line in text.splitlines():
        m = PASSAGE_HEADER_RE.match(line)
        if m:
            if current is not None:
                passages.append(current)
            name = m.group("name").strip()
            tags = m.group("tags") or ""
            tags = [t for t in tags.split() if t]
            meta_raw = m.group("meta")
            position = None
            if meta_raw:
                try:
                    meta = json.loads(meta_raw)
                    pos = meta.get("position")
                    if pos:
                        x, y = pos.split(",")
                        position = (float(x), float(y))
                except Exception:
                    pass
            current = {
                "name": name,
                "tags": tags,
                "position": position,
                "body": [],
            }
        else:
            if current is not None:
                current["body"].append(line)
    if current is not None:
        passages.append(current)
    for p in passages:
        p["body"] = "\n".join(p["body"]).rstrip()
    return passages


# ──────────────────────────────────────────────────────────────────────
# 2.  Extract links from a passage body
# ──────────────────────────────────────────────────────────────────────

LINK_BRACKET_RE = re.compile(r'\[\[([^\[\]]+?)\]\]')
LINK_GOTO_RE = re.compile(r'\(link-goto:\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\s*,\s*([^)]+?)\)')
GOTO_RE = re.compile(r'\(go-?to:\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\$\w+)\s*\)')
DISPLAY_RE = re.compile(r'\(display:\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\$\w+)\s*\)')

def _unquote(s):
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s

def parse_bracket_link(inner):
    """Parse the inside of a [[...]] — handle pipe and arrow variants."""
    # [[display|target]]   — most common in Harlowe
    if "|" in inner:
        disp, target = inner.split("|", 1)
        return disp.strip(), target.strip(), "bracket"
    # [[display->target]]
    if "->" in inner:
        disp, target = inner.split("->", 1)
        return disp.strip(), target.strip(), "bracket"
    # [[target<-display]]
    if "<-" in inner:
        target, disp = inner.split("<-", 1)
        return disp.strip(), target.strip(), "bracket"
    # [[target]] — same name for display and target
    t = inner.strip()
    return t, t, "bracket"

def extract_links(body):
    """Return (links, displays).
    links: list of {display, target, kind, dynamic}
    displays: list of target strings (passages included via (display:))
    """
    links = []
    seen = set()  # dedupe identical (display, target) pairs per passage

    def add(display, target, kind, dynamic=False):
        key = (display, target, kind, dynamic)
        if key in seen:
            return
        seen.add(key)
        links.append({"display": display, "target": target, "kind": kind, "dynamic": dynamic})

    for m in LINK_BRACKET_RE.finditer(body):
        disp, target, kind = parse_bracket_link(m.group(1))
        # A dynamic target inside [[..]] would use $var — uncommon in Harlowe but flag it
        dynamic = target.startswith("$")
        add(disp, target, "bracket", dynamic=dynamic)

    for m in LINK_GOTO_RE.finditer(body):
        disp = _unquote(m.group(1))
        target_raw = m.group(2).strip()
        if target_raw.startswith('"') or target_raw.startswith("'"):
            add(disp, _unquote(target_raw), "link-goto", dynamic=False)
        else:
            # Dynamic target like $lilyCallReturn — we can't resolve statically
            add(disp, target_raw, "link-goto", dynamic=True)

    for m in GOTO_RE.finditer(body):
        target_raw = m.group(1).strip()
        if target_raw.startswith('"') or target_raw.startswith("'"):
            add("(auto)", _unquote(target_raw), "go-to", dynamic=False)
        else:
            add("(auto)", target_raw, "go-to", dynamic=True)

    displays = []
    seen_disp = set()
    for m in DISPLAY_RE.finditer(body):
        raw = m.group(1).strip()
        if raw.startswith('"') or raw.startswith("'"):
            name = _unquote(raw)
        else:
            name = raw  # dynamic display — rare
        if name not in seen_disp:
            seen_disp.add(name)
            displays.append(name)

    return links, displays


# ──────────────────────────────────────────────────────────────────────
# 3.  Prose preview (strip macros, HTML, hooks)
# ──────────────────────────────────────────────────────────────────────

SCRIPT_RE = re.compile(r'<script\b.*?</script>', re.DOTALL | re.IGNORECASE)
STYLE_RE = re.compile(r'<style\b.*?</style>', re.DOTALL | re.IGNORECASE)
HTML_TAG_RE = re.compile(r'<[^>]+>')
HARLOWE_MACRO_RE = re.compile(r'\([a-zA-Z][a-zA-Z0-9_-]*:\s*[^()]*?\)')  # simple, one pass; iterate to handle some nesting
HOOK_LABEL_RE = re.compile(r'\|[A-Za-z0-9_]+>|<[A-Za-z0-9_]+\|')
TRAILING_BACKSLASH_RE = re.compile(r'\\\n')
MULTISPACE_RE = re.compile(r'\s+')
BRACKET_LINK_REPLACE_RE = re.compile(r'\[\[([^\[\]]+?)\]\]')

def prose_preview(body, limit=180):
    s = body
    s = SCRIPT_RE.sub(' ', s)
    s = STYLE_RE.sub(' ', s)
    # Drop Harlowe macros — iterate, since stripping one level can expose another.
    for _ in range(6):
        new = HARLOWE_MACRO_RE.sub('', s)
        if new == s:
            break
        s = new
    s = HTML_TAG_RE.sub('', s)
    s = HOOK_LABEL_RE.sub('', s)
    s = TRAILING_BACKSLASH_RE.sub('\n', s)
    # Replace [[disp|target]] with just the display text
    def _link_to_text(m):
        disp, _, _ = parse_bracket_link(m.group(1))
        return disp
    s = BRACKET_LINK_REPLACE_RE.sub(_link_to_text, s)
    # Strip any leftover loose brackets / pipes from conditional remnants
    s = s.replace('[', ' ').replace(']', ' ').replace('|', ' ')
    s = MULTISPACE_RE.sub(' ', s).strip()
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(' ', 1)[0] + '…'


# ──────────────────────────────────────────────────────────────────────
# 4.  Classify passages
# ──────────────────────────────────────────────────────────────────────

def classify(p):
    if p["name"] in SYSTEM_NAMES:
        return "system"
    if any(t in SYSTEM_TAGS for t in p["tags"]):
        return "system"
    # SVG/asset passages — used via (display:) only. Heuristic: name ends with "SVG"
    # or "Divider" — we'll refine using actual reference data below.
    return "story"


# ──────────────────────────────────────────────────────────────────────
# 5.  Strongly-connected components (Tarjan) — find cycles / loops
# ──────────────────────────────────────────────────────────────────────

def tarjan_scc(nodes, out_edges):
    index_counter = [0]
    stack = []
    lowlinks = {}
    index = {}
    on_stack = {}
    result = []

    def strongconnect(v):
        index[v] = index_counter[0]
        lowlinks[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in out_edges.get(v, ()):
            if w not in index:
                strongconnect(w)
                lowlinks[v] = min(lowlinks[v], lowlinks[w])
            elif on_stack.get(w):
                lowlinks[v] = min(lowlinks[v], index[w])
        if lowlinks[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                comp.append(w)
                if w == v:
                    break
            result.append(comp)

    # Iterative Tarjan would be safer for very deep graphs, but 136 nodes is fine.
    import sys as _sys
    _sys.setrecursionlimit(5000)
    for v in nodes:
        if v not in index:
            strongconnect(v)
    return result


# ──────────────────────────────────────────────────────────────────────
# 6.  Build the model
# ──────────────────────────────────────────────────────────────────────

def build_model():
    text = TWEE.read_text(encoding="utf-8")
    passages = parse_passages(text)

    by_name = {p["name"]: p for p in passages}

    # Get start passage from StoryData
    start = None
    sd = by_name.get("StoryData")
    if sd:
        try:
            data = json.loads("{" + sd["body"].split("{", 1)[1].rsplit("}", 1)[0] + "}")
            start = data.get("start")
        except Exception:
            pass
    # No StoryData block in this project: fall back to the same convention
    # sync_html.py uses for startnode — the passage named "Title".
    if not start and "Title" in by_name:
        start = "Title"

    # Parse links/displays for every passage
    for p in passages:
        links, displays = extract_links(p["body"])
        p["links"] = links
        p["displays"] = displays
        p["preview"] = prose_preview(p["body"])

    # Classify
    for p in passages:
        p["kind"] = classify(p)

    # Promote "asset" passages: story-classified passages that are only
    # referenced via (display:) (never linked to) and never link out.
    referenced_via_display = set()
    for p in passages:
        for d in p["displays"]:
            referenced_via_display.add(d)
    referenced_via_link = set()
    for p in passages:
        for l in p["links"]:
            if not l["dynamic"]:
                referenced_via_link.add(l["target"])

    for p in passages:
        if p["kind"] != "story":
            continue
        is_only_displayed = (
            p["name"] in referenced_via_display
            and p["name"] not in referenced_via_link
            and not p["links"]
        )
        if is_only_displayed:
            p["kind"] = "asset"

    story = [p for p in passages if p["kind"] == "story"]
    story_names = {p["name"] for p in story}

    # Build edges (story-to-story only)
    out_edges = defaultdict(list)   # name -> list of (target, kind, display, dynamic)
    in_edges = defaultdict(list)    # name -> list of (source, kind, display)
    dynamic_out = defaultdict(list) # name -> list of dynamic link records (target string is a var)
    broken = []                     # links pointing at passages that don't exist

    for p in story:
        for l in p["links"]:
            if l["dynamic"]:
                dynamic_out[p["name"]].append(l)
                continue
            target = l["target"]
            if target in story_names:
                out_edges[p["name"]].append((target, l["kind"], l["display"]))
                in_edges[target].append((p["name"], l["kind"], l["display"]))
            elif target in by_name:
                # Points at a system/asset passage — still record for the broken list? No,
                # treat as broken-for-story-graph but note the target exists.
                broken.append((p["name"], target, l["display"], "non-story-target"))
            else:
                broken.append((p["name"], target, l["display"], "missing"))

    return {
        "passages": passages,
        "by_name": by_name,
        "story": story,
        "story_names": story_names,
        "start": start,
        "out_edges": out_edges,
        "in_edges": in_edges,
        "dynamic_out": dynamic_out,
        "broken": broken,
    }


# ──────────────────────────────────────────────────────────────────────
# 7.  Render Markdown
# ──────────────────────────────────────────────────────────────────────

def render_md(model):
    L = []
    A = L.append
    story = model["story"]
    story_names = model["story_names"]
    out_edges = model["out_edges"]
    in_edges = model["in_edges"]
    dynamic_out = model["dynamic_out"]
    broken = model["broken"]
    start = model["start"]
    by_name = model["by_name"]

    # Degree stats
    indeg = {p["name"]: len(in_edges.get(p["name"], [])) for p in story}
    outdeg = {p["name"]: len(out_edges.get(p["name"], [])) for p in story}

    # Returns: pairs A↔B where A→B and B→A
    return_pairs = set()
    for src, edges in out_edges.items():
        for tgt, _k, _d in edges:
            if any(s == src for s, _, _ in in_edges.get(src, [])):
                pass  # not the test we want
            if any(t == src for t, _, _ in out_edges.get(tgt, [])):
                a, b = sorted([src, tgt])
                return_pairs.add((a, b))

    # SCCs (cycles)
    out_simple = {p["name"]: [t for t, _, _ in out_edges.get(p["name"], [])] for p in story}
    sccs = tarjan_scc([p["name"] for p in story], out_simple)
    nontrivial_sccs = [sorted(c) for c in sccs if len(c) > 1]
    nontrivial_sccs.sort(key=lambda c: -len(c))

    # Self-loops
    self_loops = [p["name"] for p in story if p["name"] in [t for t, _, _ in out_edges.get(p["name"], [])]]

    # Orphans / dead ends
    orphans = sorted([p["name"] for p in story
                      if indeg[p["name"]] == 0 and p["name"] != start])
    dead_ends = sorted([p["name"] for p in story
                        if outdeg[p["name"]] == 0 and not dynamic_out.get(p["name"])])

    # Tag groupings
    by_tag = defaultdict(list)
    for p in story:
        if not p["tags"]:
            by_tag["(untagged)"].append(p["name"])
        else:
            for t in p["tags"]:
                by_tag[t].append(p["name"])

    # ─── Header ───
    A("# Oliver Twist — Game Map")
    A("")
    A("_Auto-generated by `generate_map.py` from `Oliver Twist.twee`._")
    A("_Do not edit by hand — re-run the script to refresh._")
    A("")
    A(f"- **Total passages:** {len(model['passages'])}  ")
    A(f"- **Story passages:** {len(story)}  ")
    A(f"- **System / asset passages:** {len(model['passages']) - len(story)}  ")
    A(f"- **Start passage:** [{start}](#{anchor(start)})  " if start else "")
    A(f"- **Outgoing edges (story↔story):** {sum(len(v) for v in out_edges.values())}  ")
    A(f"- **Bidirectional pairs (returns):** {len(return_pairs)}  ")
    A(f"- **Non-trivial cycles (SCCs with >1 node):** {len(nontrivial_sccs)}")
    A("")
    A("**Jump to:**  ")
    A("[Returns](#returns) · [Cycles & loops](#cycles--loops) · "
      "[Hubs](#hubs) · [Orphans](#orphans) · [Dead ends](#dead-ends) · "
      "[Broken links](#broken-links) · [Dynamic links](#dynamic-links) · "
      "[Tags](#tags) · [Every passage](#every-passage)")
    A("")
    A("---")
    A("")

    # ─── Returns ───
    A("## Returns")
    A("")
    A("Passages that link to each other (A → B and B → A). These are the "
      "back-and-forth bridges of the game — usually venue ↔ approach, or hub ↔ spoke.")
    A("")
    if not return_pairs:
        A("_None._")
    else:
        for a, b in sorted(return_pairs):
            A(f"- [{a}](#{anchor(a)}) ↔ [{b}](#{anchor(b)})")
    A("")

    # ─── Cycles ───
    A("## Cycles & loops")
    A("")
    A("Strongly-connected components: groups of passages all mutually reachable. "
      "Each component is a loop in the game's flow.")
    A("")
    if self_loops:
        A("**Self-loops** (passage links to itself):")
        for n in self_loops:
            A(f"- [{n}](#{anchor(n)})")
        A("")
    if not nontrivial_sccs:
        A("_No multi-node cycles._")
    else:
        for i, comp in enumerate(nontrivial_sccs, 1):
            A(f"**Loop {i}** ({len(comp)} passages):")
            for n in comp:
                A(f"  - [{n}](#{anchor(n)})")
            A("")

    # ─── Hubs ───
    A("## Hubs")
    A("")
    A("Highest-traffic passages — sorted by total degree (in + out).")
    A("")
    degree_table = sorted(
        [(p["name"], indeg[p["name"]], outdeg[p["name"]]) for p in story],
        key=lambda r: -(r[1] + r[2])
    )
    A("| Passage | In | Out | Total |")
    A("|---|---:|---:|---:|")
    for name, i, o in degree_table[:20]:
        A(f"| [{name}](#{anchor(name)}) | {i} | {o} | {i + o} |")
    A("")

    # ─── Orphans ───
    A("## Orphans")
    A("")
    A("Story passages with **no incoming links** (and not the start). "
      "May be reached only via dynamic links, or may be unreachable.")
    A("")
    if not orphans:
        A("_None._")
    else:
        for n in orphans:
            A(f"- [{n}](#{anchor(n)})")
    A("")

    # ─── Dead ends ───
    A("## Dead ends")
    A("")
    A("Story passages with **no outgoing links** (and no dynamic links either). "
      "These are the endings — or accidental traps.")
    A("")
    if not dead_ends:
        A("_None._")
    else:
        for n in dead_ends:
            A(f"- [{n}](#{anchor(n)})")
    A("")

    # ─── Broken / cross-kind links ───
    A("## Broken links")
    A("")
    A("Links that point at a passage that doesn't exist, or that point from "
      "a story passage at a system/asset passage. Worth eyeballing.")
    A("")
    if not broken:
        A("_None._")
    else:
        for src, tgt, disp, reason in broken:
            tag = "missing" if reason == "missing" else "non-story target"
            A(f"- [{src}](#{anchor(src)}) → `{tgt}` "
              f"(choice: \"{disp}\") — **{tag}**")
    A("")

    # ─── Dynamic links ───
    A("## Dynamic links")
    A("")
    A("Links whose target is a variable (`$foo`) resolved at runtime. "
      "Static analysis can't follow them — listed here so you can check the code.")
    A("")
    if not dynamic_out:
        A("_None._")
    else:
        for src in sorted(dynamic_out):
            for l in dynamic_out[src]:
                A(f"- [{src}](#{anchor(src)}) → `{l['target']}` "
                  f"(choice: \"{l['display']}\", kind: {l['kind']})")
    A("")

    # ─── Tags ───
    A("## Tags")
    A("")
    A("Passages grouped by tag. A passage with multiple tags appears in each group.")
    A("")
    for tag in sorted(by_tag, key=lambda t: (t == "(untagged)", t.lower())):
        names = sorted(by_tag[tag])
        A(f"### `{tag}` ({len(names)})")
        A("")
        for n in names:
            A(f"- [{n}](#{anchor(n)})")
        A("")

    # ─── Every passage ───
    A("## Every passage")
    A("")
    A("Alphabetical. Each entry shows tags, prose preview, outgoing choices "
      "(with their player-facing text), incoming links, and any `(display:)` includes. "
      "**↔** flags an outgoing link whose target also links back here (a return).")
    A("")
    for p in sorted(story, key=lambda p: p["name"].lower()):
        name = p["name"]
        A(f"### {name}")
        A("")
        bits = []
        if name == start:
            bits.append("**START**")
        if p["tags"]:
            bits.append("tags: " + ", ".join(f"`{t}`" for t in p["tags"]))
        if p["position"]:
            bits.append(f"pos: ({int(p['position'][0])}, {int(p['position'][1])})")
        if bits:
            A(" · ".join(bits))
            A("")
        if p["preview"]:
            A(f"> {p['preview']}")
            A("")
        # Outgoing
        outs = out_edges.get(name, [])
        if outs:
            A("**Choices out:**")
            seen_pair = set()
            for tgt, kind, disp in outs:
                if (tgt, disp) in seen_pair:
                    continue
                seen_pair.add((tgt, disp))
                # Mark return if the target also links back to this passage
                tgt_outs = [t for t, _, _ in out_edges.get(tgt, [])]
                ret = " ↔" if name in tgt_outs else ""
                disp_show = f"\"{disp}\"" if disp != "(auto)" else "_(auto/go-to)_"
                kind_show = f" ·{kind}" if kind != "bracket" else ""
                A(f"- {disp_show} → [{tgt}](#{anchor(tgt)}){ret}{kind_show}")
            A("")
        # Dynamic
        dyns = dynamic_out.get(name, [])
        if dyns:
            A("**Dynamic choices out:**")
            for l in dyns:
                disp_show = f"\"{l['display']}\"" if l["display"] != "(auto)" else "_(auto)_"
                A(f"- {disp_show} → `{l['target']}` _(resolved at runtime)_")
            A("")
        # Incoming
        ins = in_edges.get(name, [])
        if ins:
            A("**Linked from:**")
            seen_in = set()
            for src, kind, disp in ins:
                if (src, disp) in seen_in:
                    continue
                seen_in.add((src, disp))
                disp_show = f"\"{disp}\"" if disp != "(auto)" else "_(auto/go-to)_"
                A(f"- [{src}](#{anchor(src)}) — {disp_show}")
            A("")
        # Displays
        if p["displays"]:
            A("**Includes via `(display:)`:**")
            for d in p["displays"]:
                A(f"- `{d}`")
            A("")
        A("---")
        A("")

    return "\n".join(L).strip() + "\n"


def anchor(name):
    """GitHub-style anchor from a heading."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = s.replace(" ", "-")
    return s


# ──────────────────────────────────────────────────────────────────────
# 8.  Render interactive HTML (vis-network)
# ──────────────────────────────────────────────────────────────────────

# Palette for the most common structural tags
TAG_COLORS = {
    "prologue":  "#8aa890",
    "hub":       "#d4a574",
    "fixed":     "#c87060",
    "excursion": "#7a86b8",
    "interlude": "#a890c8",
    "epilogue":  "#c8a060",
    "ending":    "#e0e0e0",
}
DEFAULT_COLOR = "#5a6b7d"
START_COLOR   = "#f0d080"

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>Oliver Twist — Game Map</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  html, body { margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden;
               background: #14110d; color: #d4c8b0;
               font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; }
  #wrap { position: fixed; inset: 0; display: flex; }
  #net  { flex: 1; min-width: 0; height: 100vh; background: #14110d; position: relative;
          overflow: hidden; }
  #side { width: 340px; flex: 0 0 340px; height: 100vh; padding: 16px 18px;
          overflow-y: auto; border-left: 1px solid #2a241c; background: #1a1610;
          box-sizing: border-box; }
  h1   { font-size: 14px; letter-spacing: 0.15em; text-transform: uppercase;
         color: #d4a574; margin: 0 0 10px 0; }
  h2   { font-size: 12px; letter-spacing: 0.1em; text-transform: uppercase;
         color: #b89878; margin: 16px 0 6px 0; }
  .meta { color: #8a7a5a; font-size: 11px; margin-bottom: 12px; }
  .preview { color: #c4b89c; font-size: 12px; line-height: 1.5;
             border-left: 2px solid #3a3020; padding-left: 8px; margin: 8px 0 12px 0;
             font-style: italic; }
  ul   { list-style: none; padding: 0; margin: 0; font-size: 12px; }
  li   { margin: 3px 0; padding: 2px 0; color: #b8a888; line-height: 1.4; }
  .target { color: #d4a574; cursor: pointer; }
  .target:hover { color: #f0d080; text-decoration: underline; }
  .disp { color: #8a7a5a; }
  .return { color: #d68080; font-weight: bold; }
  .tag  { display: inline-block; background: #2a241c; color: #b89878;
          padding: 1px 6px; border-radius: 2px; margin-right: 4px; font-size: 10px; }
  .controls { margin-bottom: 12px; }
  .controls input { width: 100%; background: #14110d; color: #d4c8b0;
                    border: 1px solid #2a241c; padding: 6px 8px; font-size: 12px;
                    box-sizing: border-box; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; font-size: 10px; }
  .legend span { display: inline-flex; align-items: center; gap: 4px; }
  .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
  .empty { color: #5a4a3a; font-style: italic; font-size: 11px; }
</style>
</head>
<body>
<div id="wrap">
  <div id="net"></div>
  <div id="side">
    <h1>Oliver Twist</h1>
    <div class="meta">__META__</div>
    <div class="controls">
      <input id="search" placeholder="Search passages…" />
    </div>
    <div id="detail">
      <p class="empty">Click a node to see its details. Drag the canvas to pan, scroll to zoom.</p>
    </div>
    <h2>Legend</h2>
    <div class="legend">__LEGEND__</div>
  </div>
</div>
<script>
const DATA = __DATA__;

const nodes = new vis.DataSet(DATA.nodes.map(n => ({
  id: n.id,
  label: n.label,
  color: { background: n.color, border: n.border || '#1a1610',
           highlight: { background: '#f0d080', border: '#fff' } },
  font: { color: '#d4c8b0', size: 12, face: 'Helvetica Neue' },
  shape: n.start ? 'star' : 'dot',
  size: 8 + Math.min(n.degree * 1.5, 18),
  borderWidth: n.start ? 2 : 1,
})));

const edges = new vis.DataSet(DATA.edges.map(e => ({
  from: e.from,
  to: e.to,
  arrows: 'to',
  color: { color: e.bidir ? '#a87040' : '#3a3020',
           highlight: '#f0d080' },
  width: e.bidir ? 1.2 : 0.6,
  smooth: { type: 'continuous', roundness: 0.15 },
})));

const network = new vis.Network(document.getElementById('net'),
  { nodes, edges },
  {
    autoResize: false,
    physics: {
      enabled: true,
      solver: 'forceAtlas2Based',
      forceAtlas2Based: { gravitationalConstant: -60, centralGravity: 0.01,
                          springLength: 110, springConstant: 0.06,
                          damping: 0.6, avoidOverlap: 0.5 },
      maxVelocity: 40,
      minVelocity: 0.5,
      stabilization: { enabled: true, iterations: 600, fit: true, updateInterval: 25 }
    },
    interaction: { hover: true, tooltipDelay: 200, navigationButtons: false,
                   zoomView: true, dragView: true }
  }
);
window.network = network;  // exposed for debugging only

network.once('stabilizationIterationsDone', () => {
  network.setOptions({ physics: { enabled: false } });
  network.fit({ animation: { duration: 400 } });
});

// Re-fit on window resize (since autoResize is off)
window.addEventListener('resize', () => {
  network.setSize(
    document.getElementById('net').clientWidth + 'px',
    document.getElementById('net').clientHeight + 'px'
  );
  network.redraw();
});

const byId = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));

network.on('click', params => {
  if (params.nodes.length === 0) return;
  showDetail(params.nodes[0]);
});

function showDetail(id) {
  const n = byId[id];
  if (!n) return;
  const tagsHtml = n.tags.length
    ? n.tags.map(t => `<span class="tag">${t}</span>`).join('')
    : '<span class="empty">untagged</span>';
  const outs = n.out.length
    ? n.out.map(o => {
        const ret = o.return ? ' <span class="return">↔</span>' : '';
        const disp = o.display && o.display !== '(auto)'
          ? `<span class="disp">"${escapeHtml(o.display)}"</span> →`
          : '<span class="disp">(auto)</span> →';
        return `<li>${disp} <span class="target" data-id="${escapeAttr(o.target)}">${escapeHtml(o.target)}</span>${ret}</li>`;
      }).join('')
    : '<li class="empty">none</li>';
  const dyns = n.dyn.length
    ? n.dyn.map(d => {
        const disp = d.display && d.display !== '(auto)'
          ? `<span class="disp">"${escapeHtml(d.display)}"</span> →`
          : '<span class="disp">(auto)</span> →';
        return `<li>${disp} <code>${escapeHtml(d.target)}</code></li>`;
      }).join('')
    : '';
  const ins = n.in.length
    ? n.in.map(i => {
        const disp = i.display && i.display !== '(auto)'
          ? `<span class="disp">"${escapeHtml(i.display)}"</span>`
          : '<span class="disp">(auto)</span>';
      return `<li><span class="target" data-id="${escapeAttr(i.source)}">${escapeHtml(i.source)}</span> ${disp}</li>`;
      }).join('')
    : '<li class="empty">none</li>';
  const displays = n.displays.length
    ? n.displays.map(d => `<li>${escapeHtml(d)}</li>`).join('')
    : '';
  document.getElementById('detail').innerHTML = `
    <h1>${escapeHtml(n.label)}${n.start ? ' ★' : ''}</h1>
    <div>${tagsHtml}</div>
    ${n.preview ? `<div class="preview">${escapeHtml(n.preview)}</div>` : ''}
    <h2>Choices out (${n.out.length})</h2>
    <ul>${outs}</ul>
    ${n.dyn.length ? `<h2>Dynamic choices (${n.dyn.length})</h2><ul>${dyns}</ul>` : ''}
    <h2>Linked from (${n.in.length})</h2>
    <ul>${ins}</ul>
    ${n.displays.length ? `<h2>Includes via (display:) (${n.displays.length})</h2><ul>${displays}</ul>` : ''}
  `;
  // wire up click-to-jump
  document.querySelectorAll('#detail .target').forEach(el => {
    el.addEventListener('click', () => {
      const id = el.getAttribute('data-id');
      if (byId[id]) {
        network.selectNodes([id]);
        network.focus(id, { scale: 1.0, animation: { duration: 400 } });
        showDetail(id);
      }
    });
  });
}

document.getElementById('search').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  if (!q) { network.unselectAll(); return; }
  const hits = DATA.nodes.filter(n => n.label.toLowerCase().includes(q));
  if (hits.length === 1) {
    network.selectNodes([hits[0].id]);
    network.focus(hits[0].id, { scale: 1.0, animation: { duration: 300 } });
    showDetail(hits[0].id);
  } else if (hits.length > 1 && hits.length <= 20) {
    network.selectNodes(hits.map(n => n.id));
  }
});

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }
</script>
</body>
</html>
"""

def render_html(model):
    story = model["story"]
    out_edges = model["out_edges"]
    in_edges = model["in_edges"]
    dynamic_out = model["dynamic_out"]
    start = model["start"]

    indeg = {p["name"]: len(in_edges.get(p["name"], [])) for p in story}
    outdeg = {p["name"]: len(out_edges.get(p["name"], [])) for p in story}

    def color_for(p):
        if p["name"] == start:
            return START_COLOR
        for t in p["tags"]:
            if t in TAG_COLORS:
                return TAG_COLORS[t]
        return DEFAULT_COLOR

    nodes_json = []
    for p in story:
        name = p["name"]
        # compute return flag per outgoing
        outs = []
        seen = set()
        for tgt, kind, disp in out_edges.get(name, []):
            if (tgt, disp) in seen: continue
            seen.add((tgt, disp))
            tgt_outs = [t for t, _, _ in out_edges.get(tgt, [])]
            outs.append({
                "target": tgt,
                "display": disp,
                "kind": kind,
                "return": name in tgt_outs,
            })
        ins = []
        seen = set()
        for src, kind, disp in in_edges.get(name, []):
            if (src, disp) in seen: continue
            seen.add((src, disp))
            ins.append({"source": src, "display": disp, "kind": kind})
        dyns = [{"target": l["target"], "display": l["display"], "kind": l["kind"]}
                for l in dynamic_out.get(name, [])]
        nodes_json.append({
            "id": name,
            "label": name,
            "tags": p["tags"],
            "color": color_for(p),
            "degree": indeg[name] + outdeg[name],
            "preview": p["preview"],
            "out": outs,
            "in": ins,
            "dyn": dyns,
            "displays": p["displays"],
            "start": name == start,
        })

    edges_json = []
    seen_edge = set()
    for src, edges in out_edges.items():
        for tgt, _kind, _disp in edges:
            if (src, tgt) in seen_edge: continue
            seen_edge.add((src, tgt))
            # bidir if reverse exists
            rev_outs = [t for t, _, _ in out_edges.get(tgt, [])]
            edges_json.append({
                "from": src,
                "to": tgt,
                "bidir": src in rev_outs,
            })

    legend_bits = []
    for tag, color in TAG_COLORS.items():
        legend_bits.append(f'<span><span class="swatch" style="background:{color}"></span>{tag}</span>')
    legend_bits.append(f'<span><span class="swatch" style="background:{START_COLOR}"></span>start</span>')
    legend_bits.append(f'<span><span class="swatch" style="background:{DEFAULT_COLOR}"></span>other</span>')

    meta = (f"{len(story)} story passages · "
            f"{sum(len(v) for v in out_edges.values())} edges · "
            f"start: <b>{start or '?'}</b>")

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps({
        "nodes": nodes_json, "edges": edges_json
    }))
    html = html.replace("__META__", meta)
    html = html.replace("__LEGEND__", "".join(legend_bits))
    return html


# ──────────────────────────────────────────────────────────────────────
# 9.  Main
# ──────────────────────────────────────────────────────────────────────

def main():
    if not TWEE.exists():
        raise SystemExit(f"missing: {TWEE}")
    print(f"reading {TWEE.name}…")
    model = build_model()

    print(f"  passages: {len(model['passages'])} total, "
          f"{len(model['story'])} story, "
          f"{len(model['passages']) - len(model['story'])} system/asset")
    edge_count = sum(len(v) for v in model["out_edges"].values())
    print(f"  edges:    {edge_count} story→story")
    if model["broken"]:
        print(f"  broken:   {len(model['broken'])} (see GAME-MAP.md › Broken links)")

    print(f"writing {OUT_MD.name}…")
    OUT_MD.write_text(render_md(model), encoding="utf-8")
    print(f"writing {OUT_HTML.name}…")
    OUT_HTML.write_text(render_html(model), encoding="utf-8")
    print("done. open GAME-MAP.html in a browser, or read GAME-MAP.md in any editor.")


if __name__ == "__main__":
    main()
