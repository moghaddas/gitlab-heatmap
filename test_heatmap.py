#!/usr/bin/env python3
"""Checks on the parts that have gone wrong in practice. No network, no deps.

    python3 test_heatmap.py
"""

import datetime
import json
import os
import sys
import tempfile
import urllib.error
from collections import Counter

import gitlab_heatmap as h

FAILED = []
h.time.sleep = lambda _: None  # the retry tests must not wait


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        FAILED.append(name)


def raises(name, fn, kind):
    try:
        fn()
    except kind:
        print(f"  ok   {name}")
        return
    except Exception as e:
        print(f"  FAIL {name}: raised {type(e).__name__}, want {kind.__name__}")
    else:
        print(f"  FAIL {name}: raised nothing, want {kind.__name__}")
    FAILED.append(name)


class Fake:
    """Stands in for `get`, answering from a scripted table of pages."""

    def __init__(self, pages=None, fail=None, fail_after=None):
        self.pages, self.fail, self.fail_after = pages or {}, fail, fail_after
        self.calls = 0

    def __call__(self, api, path, token, **params):
        self.calls += 1
        if self.fail and (self.fail_after is None or self.calls > self.fail_after):
            raise self.fail
        return self.pages.get((path, params.get("page", 1)), [])


class FakeUrlopen:
    """Stands in for urllib, so `get` itself can be tested."""

    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        raise AssertionError("ran out of scripted responses")


def http(code, headers=None):
    return urllib.error.HTTPError("u", code, "x", headers or {}, None)


def commit(cid, stamp, email="me@example.com"):
    return {"id": cid, "committed_date": stamp, "author_email": email}


print("the grid")
bad_days, bad_labels = [], []
for i in range(400):
    today = datetime.date(2026, 1, 1) + datetime.timedelta(days=i)
    start = h.grid_start(today, 53)
    if start.weekday() != 6:
        bad_days.append(today)
    for row, name in h.DAY_LABELS:
        if (start + datetime.timedelta(days=row)).strftime("%a") != name:
            bad_labels.append((today, name))
check("the top-left cell is always a Sunday", bad_days, [])
check("Mon, Wed and Fri sit on the rows they label", bad_labels, [])

print("\nshades")
check("zero is the empty shade", h.level(0, [0, 2, 5, 10]), 0)
check("one lands in the first shade", h.level(1, [0, 2, 5, 10]), 1)
check("the top bound stays inside", h.level(10, [0, 2, 5, 10]), 3)
check("anything above it saturates", h.level(99, [0, 2, 5, 10]), 4)
check("a single bound gives two shades", h.level(4, [0]), 1)
check("bounds are sorted and deduped", h.parse_steps("10,2,2,0"), [0, 2, 10])
raises("a fifth bound has no colour to draw with",
       lambda: h.parse_steps("0,1,2,5,10"), h.Fatal)
raises("a non-number is rejected", lambda: h.parse_steps("a,b"), h.Fatal)
raises("an empty list is rejected", lambda: h.parse_steps(""), h.Fatal)

print("\ncaptions")
check("the known placeholders fill", h.check_label("{total} in {weeks}", 53), None)
raises("an unknown placeholder is caught before the API walk",
       lambda: h.check_label("{total} commits {2026}", 53), h.Fatal)

print("\ndays are UTC")
check("an evening commit west of Greenwich moves forward",
      h.utc_day("2026-01-02T23:00:00.000-05:00"), "2026-01-03")
check("an early commit east of it moves back",
      h.utc_day("2026-01-03T01:00:00.000+02:00"), "2026-01-02")
check("a Z stamp stays put", h.utc_day("2026-01-03T09:00:00Z"), "2026-01-03")

API = "https://gitlab.example.com/api/v4"
print("\nretries")
h.urllib.request.urlopen = FakeUrlopen([http(500), http(502), http(503), http(429)])
raises("a server error is retried, not skipped on the first try",
       lambda: h.get(API, "user", "t"), urllib.error.HTTPError)
check("all four attempts were spent", h.urllib.request.urlopen.calls, 4)
h.urllib.request.urlopen = FakeUrlopen([http(429, {"Retry-After": "0"})] * 4)
raises("a rate limit is retried", lambda: h.get(API, "user", "t"),
       urllib.error.HTTPError)
h.urllib.request.urlopen = FakeUrlopen([http(404)])
raises("a 404 answers immediately", lambda: h.get(API, "user", "t"),
       urllib.error.HTTPError)
check("a 404 is not retried", h.urllib.request.urlopen.calls, 1)
h.urllib.request.urlopen = FakeUrlopen([http(401)])
raises("a bad token stops the run", lambda: h.get(API, "user", "t"), h.Fatal)

print("\ncommit counting")
mine = {"me@example.com"}
pages = {("projects/1/repository/commits", 1): [
    commit("aaa", "2026-01-02T09:00:00Z"),
    commit("bbb", "2026-01-02T11:00:00Z"),
    commit("ccc", "2026-01-03T09:00:00Z"),
    commit("ddd", "2026-01-04T09:00:00Z", "colleague@example.com"),
]}
h.get = Fake(pages)
days, clean = h.commit_days("x", "t", 1, "", "", mine, set())
check("counts every commit, not one per push", days["2026-01-02"], 2)
check("a colleague's commit is not mine", days.get("2026-01-04"), None)
check("a clean read reports clean", clean, True)

h.get = Fake(pages)
days, _ = h.commit_days("x", "t", 1, "", "", mine, {"aaa"})
check("a commit seen in a mirror is not counted twice", days["2026-01-02"], 1)

h.get = Fake(fail=http(404))
days, clean = h.commit_days("x", "t", 1, "", "", mine, set())
check("an empty project is skipped, not fatal", (dict(days), clean), ({}, False))

seen = set()
h.get = Fake(pages, fail=http(500), fail_after=1)
days, clean = h.commit_days("x", "t", 1, "", "", mine, seen)
check("half a project is thrown away, not cached as whole", dict(days), {})
check("its commit ids are released for another project", seen, set())

h.get = Fake({}, fail=h.Fatal("401"))
raises("a fatal read stays fatal inside a project",
       lambda: h.commit_days("x", "t", 1, "", "", mine, set()), h.Fatal)

print("\nthe partial-result guard")
h.projects = lambda api, token: [(i, f"p/{i}") for i in range(4)]
h.get = Fake(fail=http(404))
raises("most projects failing refuses to overwrite the cache",
       lambda: h.collect("x", "t", "", "", mine), h.Fatal)
h.projects = lambda api, token: []
check("no projects at all is not an error",
      h.collect("x", "t", "", "", mine), Counter())

print("\nidentities")
check("a given list wins over the API",
      h.identities("x", "t", ["A@b.com", " c@d.com "]), {"a@b.com", "c@d.com"})

print("\ncache")
home = os.getcwd()
try:
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        h.save_cache("bare.json", Counter({"2026-01-01": 3}))
        check("a bare filename needs no directory", h.load_cache("bare.json"),
              Counter({"2026-01-01": 3}))
        check("no half-written file is left behind",
              os.path.exists("bare.json.tmp"), False)
        h.save_cache("nested/deep/c.json", Counter({"2026-01-01": 1}))
        check("a nested path is created", os.path.exists("nested/deep/c.json"),
              True)
        check("a missing cache reads as empty", h.load_cache("nope.json"),
              Counter())
        open("broken.json", "w").write("{not json")
        raises("a corrupt cache stops the run instead of wiping the history",
               lambda: h.load_cache("broken.json"), h.Fatal)
finally:
    os.chdir(home)

print("\nrendering")
svg = h.render(Counter({"2026-01-01": 5}), 53, [0, 2, 5, 10], "{total} commits")
check("the SVG is well formed",
      svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), True)
check("the caption is escaped",
      "&quot;" in h.render(Counter(), 4, [0], 'a "{total}" b'), True)
narrow = h.render(Counter(), 2, [0, 2, 5, 10], "x")
check("nothing is drawn off the left edge", ' x="-' in narrow, False)
shades = {cls.split('"')[0] for cls in narrow.split('class="')[1:]}
check("no cell asks for a shade the stylesheet lacks",
      shades - {"cap", "ttl", "l0", "l1", "l2", "l3", "l4"}, set())

print()
if FAILED:
    sys.exit(f"{len(FAILED)} failed: {', '.join(FAILED)}")
print("all passed")
