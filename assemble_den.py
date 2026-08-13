#!/usr/bin/env python3
"""Assemble fagins-den-3d-static.html from its parts.

The den scene HTML is COMPILED from:
  1. the existing HTML's <head> (title, styles, three.js + post-processing CDN tags)
  2. the dssScene toolkit, extracted live from the DSS .twee (single source of truth)
  3. fagins-den-scene.src.js  <-- EDIT THIS, then re-run this script

Usage:  python3 assemble_den.py
Never hand-edit the assembled HTML's script blocks.
"""
import pathlib

here = pathlib.Path(__file__).parent
twee = pathlib.Path("/Users/samquill/Claude work/Dream Street Shuffle - Game Files/Dream Street Shuffle.twee")
lines = twee.read_text(encoding="utf-8").split("\n")
start = next(i for i, l in enumerate(lines) if l.startswith("// ====== dssScene"))
end = next(i for i in range(start, len(lines)) if lines[i].startswith("})();"))
toolkit = "\n".join(lines[start:end + 1])

scene = (here / "fagins-den-scene.src.js").read_text(encoding="utf-8")
cur = (here / "fagins-den-3d-static.html").read_text(encoding="utf-8")
head = cur[:cur.index("<script>\n") + len("<script>\n")]
html = head + toolkit + "\n</script>\n<script>\n" + scene + "\n</script>\n</body>\n</html>\n"
(here / "fagins-den-3d-static.html").write_text(html, encoding="utf-8")
print("assembled fagins-den-3d-static.html (%d KB)" % (len(html) // 1024))
