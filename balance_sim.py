#!/usr/bin/env python3
"""
balance_sim.py — axis / ending balance simulator for Oliver Twist.

Reads `Oliver Twist.twee` directly (source of truth) and walks every reachable
path through the story graph, tracking $innocence, $standing and every flag.

It answers the questions the skeleton's design notes leave open:
  - Which endings are reachable, and how hard is each one?
  - What is the innocence/standing spread at each ending?
  - Is the Locket "the hardest to reach, not the default"?
  - Is any passage or gated link unreachable?

Usage:
    python3 balance_sim.py              # report to stdout + BALANCE-REPORT.md
    python3 balance_sim.py --quiet      # write the report file only
    python3 balance_sim.py --trials N   # random-play sample size (default 20000)
    python3 balance_sim.py --no-sweep   # skip the gate-sensitivity re-runs

This is a read-only analysis tool. It never writes to the .twee or the HTML.
"""

import re
import sys
import random
from collections import defaultdict
from functools import lru_cache

TWEE = "Oliver Twist.twee"
REPORT = "BALANCE-REPORT.md"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Parsing the twee into passages
# ─────────────────────────────────────────────────────────────────────────────

def strip_spans(text):
    """Remove <span>...</span> wrappers (stub prose, beats, notes).

    Stub prose is commentary, not logic. It also contains backticked mentions
    of $variables that must never be evaluated. Links and (set:)s always sit
    outside the span wrappers, so removing them is safe.
    Removes innermost-first so nested stub > beat/note unwinds correctly.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)  # authoring comments
    pattern = re.compile(r"<span[^>]*>((?:(?!<span)(?!</span>).)*?)</span>", re.S)
    while True:
        new = pattern.sub("", text)
        if new == text:
            return new
        text = new


def parse_passages(src):
    """Return {name: {'tags': [...], 'body': str}} keyed by passage name."""
    passages = {}
    header = re.compile(r"^::\s*(.+?)\s*(?:\[(.*?)\])?\s*(?:\{.*?\})?\s*$")
    name, tags, buf = None, [], []
    for line in src.split("\n"):
        m = header.match(line) if line.startswith("::") else None
        if m:
            if name is not None:
                passages[name] = {"tags": tags, "body": "\n".join(buf)}
            name = m.group(1)
            tags = (m.group(2) or "").split()
            buf = []
        elif name is not None:
            buf.append(line)
    if name is not None:
        passages[name] = {"tags": tags, "body": "\n".join(buf)}
    return passages


# ─────────────────────────────────────────────────────────────────────────────
# 2. A small evaluator for the Harlowe subset the skeleton actually uses
# ─────────────────────────────────────────────────────────────────────────────

TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>"[^"]*"|'(?!s\b)[^']*')
      | (?P<num>\d+(?:\.\d+)?)
      | (?P<poss>'s\b)
      | (?P<var>\$[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>>=|<=|>|<|\+|-|\*|/|,|\(|\))
      | (?P<word>[A-Za-z_][A-Za-z0-9_-]*:?)
    )""",
    re.X,
)


@lru_cache(maxsize=None)
def tokenize(s):
    """Cached: the same handful of expressions is re-evaluated millions of
    times across the walk, but each one only ever needs tokenizing once."""
    out, i = [], 0
    while i < len(s):
        m = TOKEN.match(s, i)
        if not m:
            if s[i].isspace():
                i += 1
                continue
            raise SyntaxError(f"cannot tokenize at {s[i:i+30]!r}")
        i = m.end()
        for kind in ("str", "num", "poss", "var", "op", "word"):
            v = m.group(kind)
            if v is not None:
                out.append((kind, v))
                break
    return out


class Expr:
    """Recursive-descent compiler over the token stream.

    Each rule returns a closure of (state, it, passage) rather than a value, so
    an expression is parsed once and then evaluated millions of times cheaply.
    """

    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def accept(self, kind, val=None):
        k, v = self.peek()
        if k == kind and (val is None or v == val):
            self.i += 1
            return True
        return False

    # or > and > not > comparison > additive > unary > postfix > primary
    def parse(self):
        fn = self.p_or()
        if self.i != len(self.t):
            raise SyntaxError(f"trailing tokens: {self.t[self.i:]!r}")
        return fn

    def p_or(self):
        left = self.p_and()
        while self.peek() == ("word", "or"):
            self.take()
            # Both sides are always parsed; only evaluation short-circuits.
            a, b = left, self.p_and()
            left = lambda s, i, p, a=a, b=b: bool(a(s, i, p)) or bool(b(s, i, p))
        return left

    def p_and(self):
        left = self.p_not()
        while self.peek() == ("word", "and"):
            self.take()
            a, b = left, self.p_not()
            left = lambda s, i, p, a=a, b=b: bool(a(s, i, p)) and bool(b(s, i, p))
        return left

    def p_not(self):
        if self.peek() == ("word", "not"):
            self.take()
            a = self.p_not()
            return lambda s, i, p, a=a: not bool(a(s, i, p))
        return self.p_cmp()

    CMP = {
        ">=": lambda x, y: x >= y,
        "<=": lambda x, y: x <= y,
        ">": lambda x, y: x > y,
        "<": lambda x, y: x < y,
    }

    def p_cmp(self):
        left = self.p_add()
        k, v = self.peek()
        if k == "word" and v == "is":
            self.take()
            if self.peek() == ("word", "not"):
                self.take()
                a, b = left, self.p_add()
                return lambda s, i, p, a=a, b=b: a(s, i, p) != b(s, i, p)
            a, b = left, self.p_add()
            return lambda s, i, p, a=a, b=b: a(s, i, p) == b(s, i, p)
        if k == "op" and v in self.CMP:
            self.take()
            op, a, b = self.CMP[v], left, self.p_add()
            return lambda s, i, p, op=op, a=a, b=b: op(a(s, i, p), b(s, i, p))
        return left

    def p_add(self):
        left = self.p_unary()
        while True:
            k, v = self.peek()
            if k == "op" and v in ("+", "-"):
                self.take()
                a, b = left, self.p_unary()
                if v == "+":
                    left = lambda s, i, p, a=a, b=b: a(s, i, p) + b(s, i, p)
                else:
                    left = lambda s, i, p, a=a, b=b: a(s, i, p) - b(s, i, p)
            else:
                return left

    def p_unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            a = self.p_unary()
            return lambda s, i, p, a=a: -a(s, i, p)
        return self.p_postfix()

    def p_postfix(self):
        val = self.p_primary()
        while self.peek()[0] == "poss":
            self.take()
            _, key = self.take()
            val = self._access(val, key)
        return val

    @staticmethod
    def _access(inner, key):
        def get(s, i, p):
            v = inner(s, i, p)
            if isinstance(v, dict):
                return v.get(key, False)
            if key == "name":
                return p
            raise SyntaxError(f"unsupported 's access: {key}")
        return get

    def p_primary(self):
        k, v = self.take()
        if k == "num":
            n = float(v) if "." in v else int(v)
            return lambda s, i, p, n=n: n
        if k == "str":
            t = v[1:-1]
            return lambda s, i, p, t=t: t
        if k == "var":
            name = v[1:]
            return lambda s, i, p, name=name: s.get(name, False)
        if k == "word":
            if v == "true":
                return lambda s, i, p: True
            if v == "false":
                return lambda s, i, p: False
            if v == "it":
                return lambda s, i, p: i
            raise SyntaxError(f"unknown word {v!r}")
        if k == "op" and v == "(":
            k2, v2 = self.peek()
            if k2 == "word" and v2.endswith(":"):
                return self.p_macro(self.take()[1][:-1])
            inner = self.p_or()
            if not self.accept("op", ")"):
                raise SyntaxError("expected )")
            return inner
        raise SyntaxError(f"unexpected token {v!r}")

    def p_macro(self, name):
        args = []
        if not self.accept("op", ")"):
            while True:
                args.append(self.p_or())
                if self.accept("op", ","):
                    continue
                if self.accept("op", ")"):
                    break
                raise SyntaxError(f"bad args to ({name}:)")
        if name == "max":
            return lambda s, i, p, a=args: max(f(s, i, p) for f in a)
        if name == "min":
            return lambda s, i, p, a=args: min(f(s, i, p) for f in a)
        if name == "dm":
            return lambda s, i, p, a=args: {
                a[j](s, i, p): a[j + 1](s, i, p) for j in range(0, len(a), 2)}
        if name == "passage":
            return lambda s, i, p: {"name": p}
        if name == "print":
            return lambda s, i, p, a=args: a[0](s, i, p) if a else ""
        raise SyntaxError(f"unsupported macro ({name}:)")


@lru_cache(maxsize=None)
def compile_expr(source):
    return Expr(tokenize(source)).parse()


def evaluate(source, state, it=None, passage=None):
    return compile_expr(source)(state, it, passage)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Walking a passage body: apply (set:)s, resolve (if:) hooks, collect links
# ─────────────────────────────────────────────────────────────────────────────

LINK = re.compile(r"\[\[(.+?)\]\]")
MACRO_START = re.compile(r"\((set|if|else-if|else|link-goto|link|print):")


def link_target(inner):
    """[[Text->Target]] / [[Target<-Text]] / [[Target]] -> Target."""
    if "->" in inner:
        return inner.split("->", 1)[1].strip()
    if "<-" in inner:
        return inner.split("<-", 1)[0].strip()
    return inner.strip()


def match_hook(body, start):
    """Given index of '[', return index just past the matching ']'.

    [[...]] links are skipped atomically so their brackets never affect depth.
    """
    depth, i = 0, start
    while i < len(body):
        if body.startswith("[[", i):
            close = body.find("]]", i + 2)
            i = len(body) if close < 0 else close + 2
            continue
        if body[i] == "[":
            depth += 1
        elif body[i] == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(body)


def split_macro_args(body, open_paren):
    """Given index of '(' after a macro name, return (args, index past ')')."""
    depth, i = 0, open_paren
    in_str = None
    while i < len(body):
        c = body[i]
        if in_str:
            if c == in_str:
                in_str = None
        elif c in "\"'":
            # ' is only a string opener when not the possessive 's
            if not (c == "'" and body.startswith("'s", i)):
                in_str = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return body[open_paren + 1:i], i + 1
        i += 1
    return body[open_paren + 1:], len(body)


def compile_body(body):
    """Compile a passage body once into a flat op list.

    Ops are ('link', target) | ('set', (stmt, ...)) | (kind, cond_src, sub_ops)
    with kind in 'if' | 'else-if' | 'else'. Compiling up front matters: the walk
    executes these bodies hundreds of thousands of times, and re-scanning the
    raw text on every visit dominated the runtime.
    """
    ops = []
    i = 0
    while i < len(body):
        if body.startswith("[[", i):
            close = body.find("]]", i + 2)
            if close < 0:
                break
            ops.append(("link", link_target(body[i + 2:close])))
            i = close + 2
            continue

        m = MACRO_START.match(body, i)
        if not m:
            i += 1
            continue

        kind = m.group(1)
        args_src, after = split_macro_args(body, m.start())
        args_src = args_src[len(kind) + 1:]  # drop the leading "name:"

        if kind == "set":
            ops.append(("set", tuple(split_sets(args_src))))
            i = after
            continue

        if kind in ("if", "else-if", "else"):
            j = body.find("[", after)
            if j < 0:
                i = after
                continue
            end = match_hook(body, j)
            ops.append((kind, args_src, compile_body(body[j + 1:end - 1])))
            i = end
            continue

        if kind == "link-goto":
            parts = [p.strip() for p in split_top_commas(args_src)]
            if len(parts) >= 2:
                ops.append(("link", parts[-1].strip("\"'")))
            i = after
            continue

        # (link:) is only used for the restart control; (print:) has no effect.
        if kind == "link":
            j = body.find("[", after)
            i = match_hook(body, j) if j >= 0 else after
            continue

        i = after

    return ops


def exec_ops(ops, state, passage, trace=None):
    """Execute compiled ops against `state` (mutated). Returns link targets.

    `trace` (optional set) records gated links that WERE offered, so the caller
    can spot conditional links that no run ever unlocks.
    """
    links = []
    # Whether any branch of the current (if:)/(else-if:)/(else:) chain has
    # fired. Tracking only the previous branch is wrong: with `if` true and a
    # following `else-if` false, the trailing `else` would fire as well.
    chain_taken = False
    for op in ops:
        kind = op[0]
        if kind == "link":
            links.append(op[1])
        elif kind == "set":
            for stmt in op[1]:
                apply_set(stmt, state, passage)
        else:
            _, cond_src, sub = op
            if kind == "if":
                cond = bool(evaluate(cond_src, state, passage=passage))
                chain_taken = cond
            elif kind == "else-if":
                cond = (not chain_taken) and bool(
                    evaluate(cond_src, state, passage=passage))
                chain_taken = chain_taken or cond
            else:
                cond = not chain_taken
                chain_taken = True
            if cond:
                links.extend(exec_ops(sub, state, passage, trace))
                if trace is not None and kind in ("if", "else-if"):
                    for t in all_targets(sub):
                        trace.add((passage, t))
    return links


def all_targets(ops):
    """Every link target inside a compiled op list, at any depth."""
    out = []
    for op in ops:
        if op[0] == "link":
            out.append(op[1])
        elif op[0] != "set":
            out.extend(all_targets(op[2]))
    return out


def run_body(body, state, passage, trace=None):
    """Compile-and-run, for one-off use (StoryInit, ad-hoc probes)."""
    return exec_ops(compile_body(body), state, passage, trace)


def split_top_commas(s):
    out, depth, cur, in_str = [], 0, [], None
    for c in s:
        if in_str:
            cur.append(c)
            if c == in_str:
                in_str = None
            continue
        if c in "\"'":
            in_str = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    out.append("".join(cur))
    return out


@lru_cache(maxsize=None)
def split_sets(args_src):
    """(set: $a to 1, $b to 2) -> ['$a to 1', '$b to 2']"""
    return [p.strip() for p in split_top_commas(args_src) if p.strip()]


SET_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)((?:'s\s*[A-Za-z_]\w*)*)\s+to\s+(.*)$", re.S)


@lru_cache(maxsize=None)
def parse_set(stmt):
    m = SET_RE.match(stmt.strip())
    if not m:
        raise SyntaxError(f"unparsed set: {stmt!r}")
    var, path, rhs = m.group(1), m.group(2), m.group(3)
    return var, tuple(re.findall(r"'s\s*([A-Za-z_]\w*)", path)), rhs


def apply_set(stmt, state, passage):
    var, keys, rhs = parse_set(stmt)
    if keys:
        target = state.setdefault(var, {})
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        current = target.get(keys[-1], False)
        target[keys[-1]] = evaluate(rhs, state, it=current, passage=passage)
    else:
        state[var] = evaluate(rhs, state, it=state.get(var, False), passage=passage)


# ─────────────────────────────────────────────────────────────────────────────
# 4. The story model
# ─────────────────────────────────────────────────────────────────────────────

class Story:
    def __init__(self, path=TWEE, src=None):
        raw = src if src is not None else open(path, encoding="utf-8").read()
        self.raw = raw
        self.passages = {
            name: {"tags": p["tags"], "body": strip_spans(p["body"])}
            for name, p in parse_passages(raw).items()
        }
        self.header = next(
            (n for n, p in self.passages.items() if "header" in p["tags"]), None)
        self.init = next(
            (n for n, p in self.passages.items() if "startup" in p["tags"]), None)
        self.start = "Title"
        self.ops = {n: compile_body(p["body"]) for n, p in self.passages.items()}
        self.header_ops = self.ops.get(self.header, [])
        self.endings = {n for n, p in self.passages.items() if "ending" in p["tags"]}
        self.gated_links = self._collect_gated_links()

    def _collect_gated_links(self):
        """Every link that sits inside an (if:)/(else-if:) hook."""
        found = set()

        def scan(name, ops):
            for op in ops:
                if op[0] in ("if", "else-if"):
                    for t in all_targets(op[2]):
                        found.add((name, t))
                    scan(name, op[2])
                elif op[0] == "else":
                    scan(name, op[2])

        for name, ops in self.ops.items():
            scan(name, ops)
        return found

    def initial_state(self):
        state = {}
        if self.init:
            exec_ops(self.ops[self.init], state, self.init)
        return state

    def apply_header(self, passage, state, trace=None):
        """The [header] passage runs before every passage body — including
        endings, where its 0–100 clamp is what the player actually sees."""
        if self.header_ops:
            exec_ops(self.header_ops, state, passage, trace)

    def step(self, passage, state, trace=None):
        """Run header + body. Returns list of onward link targets."""
        self.apply_header(passage, state, trace)
        return exec_ops(self.ops[passage], state, passage, trace)

    def is_ending(self, passage):
        return passage in self.endings


def find_gates(story):
    """Every (if:)/(else-if:) condition that tests an axis, with its passage."""
    gates = []

    def scan(name, ops):
        for op in ops:
            if op[0] in ("if", "else-if"):
                cond = op[1].strip()
                if "$innocence" in cond or "$standing" in cond:
                    gates.append((name, cond, all_targets(op[2])))
            if op[0] != "set" and op[0] != "link":
                scan(name, op[2])

    for name in story.passages:
        scan(name, story.ops[name])
    return gates


def patch_gate(raw, passage, old_value, new_value):
    """Rewrite one numeric threshold inside one passage of the raw twee.

    Used only to build throwaway in-memory variants for the sensitivity sweep;
    the file on disk is never touched.
    """
    start = raw.index("\n:: " + passage + " ") if "\n:: " + passage + " " in raw \
        else raw.index("\n:: " + passage + "\n")
    nxt = raw.find("\n:: ", start + 1)
    nxt = len(raw) if nxt < 0 else nxt
    body = raw[start:nxt]
    patched = re.sub(r"(\$innocence\s*>=\s*)%d\b" % old_value,
                     r"\g<1>%d" % new_value, body, count=1)
    return raw[:start] + patched + raw[nxt:]


def locket_gate(story):
    """Find the (passage, threshold) guarding the hardest ending, if any."""
    for name, cond, targets in find_gates(story):
        if any(t in story.endings for t in targets):
            m = re.search(r"\$innocence\s*>=\s*(\d+)", cond)
            if m:
                return name, int(m.group(1)), targets[0]
    return None


def freeze(state):
    def f(v):
        return tuple(sorted(v.items())) if isinstance(v, dict) else v
    return tuple(sorted((k, f(v)) for k, v in state.items()))


# ─────────────────────────────────────────────────────────────────────────────
# 5. Exhaustive walk (memoised on passage + state)
# ─────────────────────────────────────────────────────────────────────────────

class Stats:
    __slots__ = ("paths", "inn_min", "inn_max", "std_min", "std_max",
                 "flags_true", "flags_false", "best_path", "worst_path", "depth_min")

    def __init__(self):
        self.paths = 0
        self.inn_min = self.std_min = 10 ** 9
        self.inn_max = self.std_max = -10 ** 9
        self.flags_true = set()
        self.flags_false = set()
        self.best_path = None
        self.worst_path = None

    def add_leaf(self, state, path):
        self.paths += 1
        inn, std = state.get("innocence", 0), state.get("standing", 0)
        if inn > self.inn_max:
            self.inn_max, self.best_path = inn, path
        if inn < self.inn_min:
            self.inn_min, self.worst_path = inn, path
        self.std_min, self.std_max = min(self.std_min, std), max(self.std_max, std)
        for k, v in state.items():
            if isinstance(v, bool):
                (self.flags_true if v else self.flags_false).add(k)

    def merge(self, other, prefix):
        self.paths += other.paths
        if other.inn_max > self.inn_max:
            self.inn_max = other.inn_max
            self.best_path = prefix + other.best_path
        if other.inn_min < self.inn_min:
            self.inn_min = other.inn_min
            self.worst_path = prefix + other.worst_path
        self.std_min = min(self.std_min, other.std_min)
        self.std_max = max(self.std_max, other.std_max)
        self.flags_true |= other.flags_true
        self.flags_false |= other.flags_false


def exhaustive(story, max_nodes=4_000_000):
    memo = {}
    visited_passages = set()
    offered_links = set()
    overflow = [False]

    def walk(passage, state):
        visited_passages.add(passage)
        if story.is_ending(passage):
            story.apply_header(passage, state)  # clamp, as the player sees it
            s = Stats()
            s.add_leaf(state, ())
            return {passage: s}

        key = (passage, freeze(state))
        if key in memo:
            return memo[key]
        if len(memo) >= max_nodes:
            overflow[0] = True
            return {}
        memo[key] = {}  # cycle guard

        links = story.step(passage, state, offered_links)
        result = defaultdict(Stats)
        for target in links:
            if target not in story.passages:
                continue
            child = walk(target, dict_copy(state))
            for ending, st in child.items():
                result[ending].merge(st, (target,))
        result = dict(result)
        memo[key] = result
        return result

    state = story.initial_state()
    totals = walk(story.start, state)
    return totals, visited_passages, offered_links, overflow[0]


def dict_copy(state):
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in state.items()}


# ─────────────────────────────────────────────────────────────────────────────
# 6. Random-play sampling ("is it easy by accident?")
# ─────────────────────────────────────────────────────────────────────────────

def random_play(story, trials, seed=7):
    rng = random.Random(seed)
    tally = defaultdict(int)
    inn_at_end = defaultdict(list)
    for _ in range(trials):
        state = story.initial_state()
        passage, guard = story.start, 0
        while guard < 500:
            guard += 1
            if story.is_ending(passage):
                story.apply_header(passage, state)  # clamp, as the player sees it
                tally[passage] += 1
                inn_at_end[passage].append(state.get("innocence", 0))
                break
            links = [t for t in story.step(passage, state) if t in story.passages]
            if not links:
                tally["<dead end: %s>" % passage] += 1
                break
            passage = rng.choice(links)
    return tally, inn_at_end


# ─────────────────────────────────────────────────────────────────────────────
# 7. Report
# ─────────────────────────────────────────────────────────────────────────────

def flag_extremes(totals):
    """Flags that are true at every ending, and flags true at none."""
    seen_true, seen_false = set(), set()
    for s in totals.values():
        seen_true |= s.flags_true
        seen_false |= s.flags_false
    return seen_true - seen_false, seen_false - seen_true


def run_sweep(story, values=range(50, 85, 5)):
    """Re-run the exhaustive walk with the hardest ending's gate moved."""
    gate = locket_gate(story)
    if not gate:
        return None
    passage, base, ending = gate
    rows = []
    for value in values:
        variant = Story(src=patch_gate(story.raw, passage, base, value))
        totals, _, _, _ = exhaustive(variant)
        total = sum(s.paths for s in totals.values())
        hit = totals[ending].paths if ending in totals else 0
        rows.append((value, hit, 100.0 * hit / total if total else 0.0))
    return {"gate": gate, "rows": rows}


def build_report(story, totals, visited, offered, overflow, tally, inn_at_end,
                 trials, sweep=None):
    L = []
    w = L.append
    w("# Balance report — Oliver Twist")
    w("")
    w("Generated by `balance_sim.py` from `Oliver Twist.twee`. "
      "Read-only analysis; nothing here edits the game.")
    w("")

    total_paths = sum(s.paths for s in totals.values())
    w(f"**{total_paths:,} distinct playthroughs** reach an ending "
      f"({len(totals)} of {sum(1 for p in story.passages.values() if 'ending' in p['tags'])} "
      f"endings reachable).")
    if overflow:
        w("")
        w("> ⚠️ State-space cap hit — counts are a lower bound.")
    w("")

    w("## Endings by exhaustive path count")
    w("")
    w("Every distinct sequence of player choices counts once. This measures how "
      "much of the *choice space* leads where — not how a real player behaves.")
    w("")
    w("| Ending | Paths | Share | Innocence at end | Standing at end |")
    w("|---|---:|---:|---|---|")
    for name, s in sorted(totals.items(), key=lambda kv: -kv[1].paths):
        share = 100.0 * s.paths / total_paths if total_paths else 0
        w(f"| {name.replace('Ending: ', '')} | {s.paths:,} | {share:.1f}% | "
          f"{s.inn_min}–{s.inn_max} | {s.std_min}–{s.std_max} |")
    w("")

    w(f"## Endings by random play ({trials:,} trials)")
    w("")
    w("A player choosing uniformly at random at every menu. This is the "
      "*accidental* baseline — what the shape of the graph gives you with no "
      "intent behind it.")
    w("")
    w("| Ending | Trials | Share | Mean innocence |")
    w("|---|---:|---:|---:|")
    for name, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        vals = inn_at_end.get(name, [])
        mean = sum(vals) / len(vals) if vals else 0
        w(f"| {name.replace('Ending: ', '')} | {n:,} | {100.0*n/trials:.1f}% | {mean:.1f} |")
    w("")

    unreached = sorted(
        n for n, p in story.passages.items()
        if n not in visited
        and not ({"startup", "header", "stylesheet", "script"} & set(p["tags"]))
        and n not in ("StoryTitle", "StoryData")
    )
    w("## Reachability")
    w("")
    if unreached:
        w("Passages never reached on any path:")
        w("")
        for n in unreached:
            w(f"- `{n}`")
    else:
        w("Every story passage is reachable. ✅")
    w("")

    never = sorted(story.gated_links - offered)
    if never:
        w("Gated links that no run ever unlocks:")
        w("")
        for src, dst in never:
            w(f"- `{src}` → `{dst}`")
    else:
        w("Every gated link opens on at least one path. ✅")
    w("")

    w("## Gates")
    w("")
    w("Every conditional in the game that tests an axis.")
    w("")
    w("| Passage | Condition | Opens |")
    w("|---|---|---|")
    for name, cond, targets in find_gates(story):
        w(f"| {name} | `{cond}` | {', '.join(targets) or '—'} |")
    w("")

    always, never = flag_extremes(totals)
    if always:
        w("**Flags true at every single ending** — anything listed here is "
          "settled before the endings and cannot discriminate between them:")
        w("")
        for f in sorted(always):
            w(f"- `${f}`")
        w("")
    if never:
        w("**Flags false at every single ending:**")
        w("")
        for f in sorted(never):
            w(f"- `${f}`")
        w("")

    if sweep:
        gate_passage, base, ending = sweep["gate"]
        w("## Gate sensitivity")
        w("")
        w(f"Moving the `$innocence` threshold in **{gate_passage}** "
          f"(currently {base}) and re-running the exhaustive walk. "
          f"Share is of all {total_paths:,} playthroughs.")
        w("")
        w(f"| Threshold | {ending.replace('Ending: ', '')} paths | Share |")
        w("|---:|---:|---:|")
        for value, paths, share in sweep["rows"]:
            mark = "  ← current" if value == base else ""
            w(f"| {value} | {paths:,} | {share:.1f}%{mark} |")
        w("")

    w("## Extremes")
    w("")
    for name, s in sorted(totals.items()):
        w(f"**{name}** — innocence {s.inn_min}–{s.inn_max}, standing {s.std_min}–{s.std_max}")
        if s.best_path:
            w("")
            w(f"- highest-innocence route: {' → '.join(s.best_path[-6:])}")
        if s.worst_path:
            w(f"- lowest-innocence route: {' → '.join(s.worst_path[-6:])}")
        w("")

    return "\n".join(L)


def main():
    args = sys.argv[1:]
    quiet = "--quiet" in args
    trials = 20000
    if "--trials" in args:
        trials = int(args[args.index("--trials") + 1])

    story = Story()
    totals, visited, offered, overflow = exhaustive(story)
    tally, inn_at_end = random_play(story, trials)
    sweep = None if "--no-sweep" in args else run_sweep(story)
    report = build_report(story, totals, visited, offered, overflow,
                          tally, inn_at_end, trials, sweep)

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    if not quiet:
        print(report)
    print(f"\n[written to {REPORT}]", file=sys.stderr)


if __name__ == "__main__":
    main()
