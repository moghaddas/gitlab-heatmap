#!/usr/bin/env python3
"""Checks on the parts that have gone wrong in practice. No network, no deps.

    python3 test_heatmap.py
"""

import os
import sys
import tempfile
import urllib.error
from collections import Counter

import gitlab_heatmap as h

FAILED = []


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

    def __init__(self, pages, fail=None):
        self.pages, self.fail, self.calls = pages, fail, 0

    def __call__(self, api, path, token, **params):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.pages.get((path, params.get("page", 1)), [])


def commit(cid, day, email="me@example.com"):
    return {"id": cid, "committed_date": f"{day}T09:00:00Z", "author_email": email}


print("levels")
check("zero is the empty shade", h.level(0, [0, 2, 5, 10]), 0)
check("one lands in the first shade", h.level(1, [0, 2, 5, 10]), 1)
check("the top bound stays inside", h.level(10, [0, 2, 5, 10]), 3)
check("anything above it saturates", h.level(99, [0, 2, 5, 10]), 4)
check("a single bound gives two shades", h.level(4, [0]), 1)

print("\nidentities")
check("a given list wins over the API",
      h.identities("x", "t", ["A@b.com", " c@d.com "]), {"a@b.com", "c@d.com"})

print("\ncommit counting")
mine = {"me@example.com"}
pages = {("projects/1/repository/commits", 1): [
    commit("aaa", "2026-01-02"),
    commit("bbb", "2026-01-02"),
    commit("ccc", "2026-01-03"),
    commit("ddd", "2026-01-04", "colleague@example.com"),
]}
h.get = Fake(pages)
days, clean = h.commit_days("x", "t", 1, "", "", mine, set())
check("counts every commit, not one per push", days["2026-01-02"], 2)
check("a colleague's commit is not mine", days.get("2026-01-04"), None)
check("a clean read reports clean", clean, True)

seen = {"aaa"}
h.get = Fake(pages)
days, _ = h.commit_days("x", "t", 1, "", "", mine, seen)
check("a commit seen in a mirror is not counted twice", days["2026-01-02"], 1)

h.get = Fake({}, fail=urllib.error.HTTPError("u", 404, "gone", {}, None))
days, clean = h.commit_days("x", "t", 1, "", "", mine, set())
check("an empty project is skipped, not fatal", (dict(days), clean), ({}, False))

h.get = Fake({}, fail=h.Fatal("401"))
raises("a 401 stops the run",
       lambda: h.commit_days("x", "t", 1, "", "", mine, set()), h.Fatal)

print("\nthe empty-result guard")
h.projects = lambda api, token: [(1, "a/b"), (2, "c/d")]
h.get = Fake({}, fail=urllib.error.HTTPError("u", 500, "boom", {}, None))
raises("every project failing refuses to overwrite the cache",
       lambda: h.collect("x", "t", "", "", mine), h.Fatal)

h.projects = lambda api, token: []
check("no projects at all is not an error", h.collect("x", "t", "", "", mine),
      Counter())

print("\ncache")
with tempfile.TemporaryDirectory() as d:
    os.chdir(d)
    h.save_cache("bare.json", Counter({"2026-01-01": 3}))
    check("a bare filename needs no directory", h.load_cache("bare.json"),
          Counter({"2026-01-01": 3}))
    h.save_cache("nested/deep/c.json", Counter({"2026-01-01": 1}))
    check("a nested path is created", os.path.exists("nested/deep/c.json"), True)
    check("a missing cache reads as empty", h.load_cache("nope.json"), Counter())

print("\nrendering")
svg = h.render(Counter({"2026-01-01": 5}), 53, [0, 2, 5, 10], "{total} commits")
check("the SVG is well formed", svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), True)
check("the caption is escaped",
      "&quot;" in h.render(Counter(), 4, [0], 'a "{total}" b'), True)
# One week of day cells, plus the two legend swatches a single bound makes.
one_week = h.render(Counter(), 1, [0], "x")
check("a narrow window still draws", 1 <= one_week.count("<rect") <= 7 + 2, True)

print()
if FAILED:
    sys.exit(f"{len(FAILED)} failed: {', '.join(FAILED)}")
print("all passed")
