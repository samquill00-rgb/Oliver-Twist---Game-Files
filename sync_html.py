#!/usr/bin/env python3
"""Sync the Twee file to the HTML file.

Same pipeline as Dream Street Shuffle: the .twee is the source of truth,
the .html is a compiled artifact. This script rewrites every
<tw-passagedata> element, the <style id='twine-user-stylesheet'> tag and
the <script id='twine-user-script'> tag inside the existing HTML shell.

Run from this directory:  python3 sync_html.py

NEVER hand-edit the .html — it gets overwritten.
"""

import re
import html
import os
import base64
import json

_here = os.path.dirname(os.path.abspath(__file__))
twee_path = os.path.join(_here, "Oliver Twist.twee")
html_path = os.path.join(_here, "Oliver Twist.html")

# ============================================================
# Asset embedding configuration
# ============================================================
# Each entry is a (placeholder, source-file, mime) tuple. At sync time every
# placeholder string found in the UserScript / stylesheet / passages is
# replaced with a base64 data URI of its source file, so the compiled HTML
# runs from file:// with no server and nothing can 404.
#
# Empty for now. Add entries as audio and images arrive — same convention as
# DSS: pick a placeholder name, drop the file in this directory, add a tuple.
AUDIO_EMBEDS = [
    # ("__OT_MUSIC_DATA_URI__", "theme.m4a", "audio/mp4"),
]

IMAGE_EMBEDS = [
    # ("__OT_LOCKET_DATA_URI__", "locket.png", "image/png"),
]

# The passage whose PID becomes the story's start node.
START_PASSAGE = "Title"

# ============================================================
# 1. Parse the Twee file
# ============================================================

with open(twee_path, "r", encoding="utf-8") as f:
    twee_content = f.read()

# :: Name [tags] {"position":"x,y","size":"w,h"}  — tags and metadata optional
passage_pattern = re.compile(
    r'^:: (.+?)(?:\s+\[([^\]]*)\])?(?:\s+(\{[^\n]+\}))?\s*$',
    re.MULTILINE
)

passages = []
stylesheet_content = None
userscript_content = None
matches = list(passage_pattern.finditer(twee_content))

for i, match in enumerate(matches):
    name = match.group(1).strip()
    tags = match.group(2) or ""
    metadata = match.group(3) or ""

    start = match.end()
    end = matches[i + 1].start() if i + 1 < len(matches) else len(twee_content)
    content = twee_content[start:end].strip()

    if name in ("StoryTitle", "StoryData"):
        continue

    if name in ("StoryStylesheet", "UserStylesheet") or tags.strip() == "stylesheet":
        stylesheet_content = content
        continue

    if name == "UserScript" or tags.strip() == "script":
        userscript_content = content
        continue

    pos_match = re.search(r'"position"\s*:\s*"([^"]+)"', metadata) if metadata else None
    size_match = re.search(r'"size"\s*:\s*"([^"]+)"', metadata) if metadata else None

    passages.append({
        "name": name,
        "tags": tags.strip(),
        "position": pos_match.group(1) if pos_match else "0,0",
        "size": size_match.group(1) if size_match else "100,100",
        "content": content,
    })

print(f"Parsed {len(passages)} passages from Twee file")
if stylesheet_content:
    print(f"Found stylesheet ({len(stylesheet_content)} chars)")
if userscript_content:
    print(f"Found UserScript ({len(userscript_content)} chars)")

# ============================================================
# 2. Update the HTML file
# ============================================================

with open(html_path, "r", encoding="utf-8") as f:
    html_content = f.read()


def encode_content(text):
    """Encode passage content for storage in an HTML attribute-ish context."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&#x27;")
    return text


passage_elements = []
for i, p in enumerate(passages, start=1):
    tags_attr = f' tags="{html.escape(p["tags"])}"' if p["tags"] else ""
    passage_elements.append(
        f'<tw-passagedata pid="{i}" name="{html.escape(p["name"])}"'
        f'{tags_attr}'
        f' position="{p["position"]}" size="{p["size"]}">'
        f'{encode_content(p["content"])}</tw-passagedata>'
    )

startnode = "1"
for i, p in enumerate(passages, start=1):
    if p["name"] == START_PASSAGE:
        startnode = str(i)
        break
print(f"Start node ({START_PASSAGE}): PID {startnode}")

html_content = re.sub(r'startnode="\d+"', f'startnode="{startnode}"', html_content)

# --- Build the asset substitutions once, apply everywhere ---
replacements = []
for placeholder, source_file, mime in IMAGE_EMBEDS + AUDIO_EMBEDS:
    asset_path = os.path.join(_here, source_file)
    if not os.path.exists(asset_path):
        print(f"WARNING: asset not found: {source_file} — {placeholder} not embedded")
        continue
    with open(asset_path, "rb") as af:
        b64 = base64.b64encode(af.read()).decode("ascii")
    replacements.append((placeholder, "data:" + mime + ";base64," + b64))
    print(f"Embedded: {source_file} ({len(b64)//1024} KB base64) -> {placeholder}")


def embed_assets(text):
    for placeholder, data_uri in replacements:
        text = text.replace(placeholder, data_uri)
    return text


# --- Stylesheet (raw CSS: lives inside <style>, so no HTML encoding) ---
if stylesheet_content:
    css_match = re.search(
        r'(<style role="stylesheet" id="twine-user-stylesheet" type="text/twine-css">).*?(</style>)',
        html_content, flags=re.DOTALL
    )
    if css_match:
        html_content = (html_content[:css_match.start()] + css_match.group(1)
                        + embed_assets(stylesheet_content) + css_match.group(2)
                        + html_content[css_match.end():])
        print("Updated stylesheet")
    else:
        print("WARNING: could not find twine-user-stylesheet tag in HTML")

# --- UserScript (raw JS: lives inside <script>, so no HTML encoding) ---
if userscript_content:
    js_match = re.search(
        r'(<script role="script" id="twine-user-script" type="text/twine-javascript">).*?(</script>)',
        html_content, flags=re.DOTALL
    )
    if js_match:
        html_content = (html_content[:js_match.start()] + js_match.group(1)
                        + embed_assets(userscript_content) + js_match.group(2)
                        + html_content[js_match.end():])
        print("Updated UserScript")
    else:
        print("WARNING: could not find twine-user-script tag in HTML")

# --- Replace all passage data ---
# Scope the cleanup regex to ONLY the <tw-storydata>…</tw-storydata> block.
# Run over the whole document, a literal "<tw-passagedata>" inside a JS string
# or comment would match and the non-greedy .*? would scan forward and eat the
# real block. (This bit is inherited from DSS, where it caused exactly that.)
_sd_open = re.search(r'<tw-storydata[^>]*>', html_content)
_sd_close = html_content.find('</tw-storydata>', _sd_open.end()) if _sd_open else -1
if _sd_open and _sd_close >= 0:
    _sd_inner = re.sub(r'<tw-passagedata[^>]*>.*?</tw-passagedata>', '',
                       html_content[_sd_open.end():_sd_close], flags=re.DOTALL)
    html_content = html_content[:_sd_open.end()] + _sd_inner + html_content[_sd_close:]
else:
    print("WARNING: could not locate <tw-storydata> block — skipping passage cleanup")

html_content = html_content.replace(
    "</tw-storydata>",
    embed_assets("\n".join(passage_elements)) + "\n</tw-storydata>"
)

# --- Keep the game out of search indexes ---
# The build is hosted on a public GitHub Pages URL that is deliberately
# unlinked ("hidden"). noindex stops crawlers listing it if the URL ever
# leaks. Applied on every sync so a rebuilt shell can't silently lose it.
NOINDEX = '<meta name="robots" content="noindex, nofollow">'
if NOINDEX not in html_content:
    html_content = html_content.replace("<head>", "<head>" + NOINDEX, 1)
    print("Added noindex meta")

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"Wrote {len(passages)} passages to HTML file ({len(html_content)//1024} KB)")
print("Done!")
