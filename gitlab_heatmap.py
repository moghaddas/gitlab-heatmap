#!/usr/bin/env python3
"""Draw a GitHub-style contribution heatmap from GitLab commit history.

Counts commits, not push events. GitLab's events API reports one event per
push, so a push carrying ten commits shows as a single contribution, and the
endpoint drops anything older than about three years. This reads
/projects/:id/repository/commits instead, which counts every commit and keeps
the whole history.

A full walk over every project is slow, so a run caches what it found and asks
GitLab only for the recent window after that.

    gitlab_heatmap.py --backfill       first run, walks the whole history
    gitlab_heatmap.py                  later runs, refresh the last --window days

Needs GITLAB_TOKEN with the read_api scope. Writes an SVG and a JSON cache.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

DEFAULT_URL = "https://gitlab.com"


class Fatal(Exception):
    """An error that must stop the run instead of skipping one project."""


# --- GitLab ------------------------------------------------------------------


def get(api, path, token, **params):
    """One API read, retried on a timeout.

    A busy project answers slowly enough to time out on the first ask, and a
    dropped page silently removes whole months from the history rather than
    failing the run, so the retry here is what keeps the graph honest.
    """
    url = f"{api}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # A bad or expired token answers 401 on every project. Skipping
            # those would delete the refetched window from the cache and
            # replace it with nothing, so the graph would lose three weeks
            # and the run would still report success.
            if e.code in (401, 403):
                raise Fatal(f"GitLab returned {e.code}. Check the token and "
                            f"its read_api scope.")
            raise  # 404 on an empty project is an answer, not a failure
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise last


def identities(api, token, given):
    """The commit author addresses that count as you.

    Commits carry whichever address git was configured with at the time, so
    one person owns several. Anything outside this set belongs to a colleague.
    """
    if given:
        return {a.strip().lower() for a in given if a.strip()}
    me = get(api, "user", token)
    found = {me[k].lower() for k in ("email", "commit_email", "public_email")
             if me.get(k)}
    found.add(f"{me['id']}-{me['username']}@users.noreply.gitlab.com".lower())
    return found


def projects(api, token):
    out, page = [], 1
    while True:
        batch = get(api, "projects", token, membership="true", simple="true",
                    per_page=100, page=page)
        if not batch:
            return out
        out += [(p["id"], p["path_with_namespace"]) for p in batch]
        page += 1


def commit_days(api, token, project_id, since, until, mine, seen):
    """Days on which one of `mine` committed, and how many times.

    Reads the default branch only, which is what a contribution graph means
    and what GitHub counts. Asking for `all=true` instead returns one copy of
    a commit per branch that reaches it, which inflates a busy day by an order
    of magnitude and breaks paging over the duplicated set. `seen` carries
    commit ids across projects, so a fork or a mirror cannot double-count.
    """
    days, page = Counter(), 1
    while True:
        try:
            batch = get(api, f"projects/{project_id}/repository/commits", token,
                        since=since, until=until, per_page=100, page=page)
        except Fatal:
            raise
        except Exception as e:
            # An empty or archived project answers 404. That is not a failure
            # of the run, so record nothing and move on.
            print(f"  skipped {project_id}: {e}", file=sys.stderr)
            return days, False
        if not batch:
            return days, True
        for c in batch:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            if (c.get("author_email") or "").lower() in mine:
                days[c["committed_date"][:10]] += 1
        page += 1


def collect(api, token, since, until, mine):
    total, seen, ok, skipped = Counter(), set(), 0, 0
    found_projects = projects(api, token)
    for pid, name in found_projects:
        days, clean = commit_days(api, token, pid, since, until, mine, seen)
        ok, skipped = (ok + 1, skipped) if clean else (ok, skipped + 1)
        if days:
            print(f"  {name}: {sum(days.values())} commits", file=sys.stderr)
        total.update(days)
    if found_projects and not ok:
        raise Fatal(f"every one of {skipped} projects failed to read. "
                    f"Refusing to overwrite the cache with an empty result.")
    return total


# --- cache -------------------------------------------------------------------


def load_cache(path):
    try:
        with open(path) as f:
            return Counter(json.load(f))
    except FileNotFoundError:
        return Counter()


def save_cache(path, counts):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(dict(sorted(counts.items())), f, indent=0, sort_keys=True)
        f.write("\n")


# --- rendering ---------------------------------------------------------------

CELL, GAP, RADIUS = 11, 3, 2
TOP, LEFT = 20, 30
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def level(n, steps):
    for i, edge in enumerate(steps):
        if n <= edge:
            return i
    return len(steps)


def render(counts, weeks, steps, label):
    today = date.today()
    # GitHub's grid starts on a Sunday, so walk back to the Sunday that puts
    # the current week in the last column.
    end = today + timedelta(days=(6 - today.weekday()) % 7)
    start = end - timedelta(weeks=weeks - 1, days=6)

    width = LEFT + weeks * (CELL + GAP) + 10
    height = TOP + 7 * (CELL + GAP) + 34

    cells, labels, seen_month = [], [], None
    for w in range(weeks):
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            if day > today:
                continue
            n = counts.get(day.isoformat(), 0)
            x = LEFT + w * (CELL + GAP)
            y = TOP + d * (CELL + GAP)
            title = f"{n} commit{'s' if n != 1 else ''} on {day.isoformat()}"
            cells.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
                f'rx="{RADIUS}" class="l{level(n, steps)}"><title>{title}</title></rect>'
            )
        first = start + timedelta(weeks=w)
        if first.month != seen_month and first.day <= 7:
            seen_month = first.month
            labels.append(
                f'<text x="{LEFT + w * (CELL + GAP)}" y="{TOP - 6}" '
                f'class="cap">{MONTHS[first.month - 1]}</text>'
            )

    for d, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        labels.append(
            f'<text x="0" y="{TOP + d * (CELL + GAP) + 9}" class="cap">{name}</text>'
        )

    total = sum(v for k, v in counts.items()
                if start.isoformat() <= k <= today.isoformat())
    caption = label.format(total=total, weeks=weeks)

    legend_x = width - 150
    legend = [f'<text x="{legend_x - 30}" y="{height - 12}" class="cap">Less</text>']
    for i in range(len(steps) + 1):
        legend.append(
            f'<rect x="{legend_x + i * (CELL + GAP)}" y="{height - 22}" '
            f'width="{CELL}" height="{CELL}" rx="{RADIUS}" class="l{i}"/>'
        )
    legend.append(
        f'<text x="{legend_x + (len(steps) + 1) * (CELL + GAP) + 4}" '
        f'y="{height - 12}" class="cap">More</text>'
    )

    nl = "\n  "
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" role="img" aria-label="{esc(caption)}">
  <style>
    .cap {{ font: 9px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #656d76; }}
    .ttl {{ font: 600 11px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #1f2328; }}
    .l0 {{ fill: #ebedf0; }} .l1 {{ fill: #9be9a8; }} .l2 {{ fill: #40c463; }}
    .l3 {{ fill: #30a14e; }} .l4 {{ fill: #216e39; }}
    @media (prefers-color-scheme: dark) {{
      .cap {{ fill: #8b949e; }} .ttl {{ fill: #e6edf3; }}
      .l0 {{ fill: #161b22; }} .l1 {{ fill: #0e4429; }} .l2 {{ fill: #006d32; }}
      .l3 {{ fill: #26a641; }} .l4 {{ fill: #39d353; }}
    }}
  </style>
  <text x="0" y="{height - 12}" class="ttl">{esc(caption)}</text>
  {nl.join(labels)}
  {nl.join(cells)}
  {nl.join(legend)}
</svg>
"""


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


# --- entry point -------------------------------------------------------------


def csv(value):
    return [p for p in (value or "").replace("\n", ",").split(",") if p.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("GITLAB_URL", DEFAULT_URL),
                    help="GitLab base URL, for a self-hosted instance")
    ap.add_argument("--identity", action="append", default=[],
                    help="a commit author address that counts as you, repeatable")
    ap.add_argument("--out", default="gitlab-heatmap.svg", help="SVG to write")
    ap.add_argument("--cache", default="data/gitlab-contributions.json",
                    help="JSON history to keep between runs")
    ap.add_argument("--backfill", action="store_true",
                    help="walk the whole history instead of the recent window")
    ap.add_argument("--since", default="2015-01-01", help="backfill start date")
    ap.add_argument("--window", type=int, default=21,
                    help="days to refresh on a normal run")
    ap.add_argument("--weeks", type=int, default=53, help="weeks to draw")
    ap.add_argument("--steps", default="0,2,5,10",
                    help="upper bound of each shade, lightest first")
    ap.add_argument("--label", default="{total} commits on GitLab in the last year",
                    help="caption, with {total} and {weeks} available")
    a = ap.parse_args()

    if a.weeks < 1 or a.weeks > 53:
        sys.exit("--weeks must be between 1 and 53")
    try:
        steps = sorted(int(s) for s in csv(a.steps))
    except ValueError:
        sys.exit("--steps must be a comma-separated list of whole numbers")
    if not steps:
        sys.exit("--steps needs at least one bound")

    api = a.url.rstrip("/") + "/api/v4"
    token = os.environ.get("GITLAB_TOKEN")
    counts = load_cache(a.cache)

    if token:
        try:
            mine = identities(api, token, a.identity or csv(
                os.environ.get("GITLAB_IDENTITIES")))
            now = datetime.now(timezone.utc)
            since = (f"{a.since}T00:00:00Z" if a.backfill
                     else (now - timedelta(days=a.window)).strftime(
                         "%Y-%m-%dT%H:%M:%SZ"))
            print(f"counting commits by {', '.join(sorted(mine))} "
                  f"since {since[:10]}", file=sys.stderr)
            fresh = collect(api, token, since,
                            now.strftime("%Y-%m-%dT%H:%M:%SZ"), mine)
        except Fatal as e:
            sys.exit(f"stopping: {e}")
        # The refetched window is authoritative, so drop the cached copy of
        # those days first. Otherwise a rebased or amended commit counts twice.
        for day in [d for d in counts if d >= since[:10]]:
            del counts[day]
        counts.update(fresh)
        save_cache(a.cache, counts)
        print(f"cached {len(counts)} active days, {sum(counts.values())} commits",
              file=sys.stderr)
    else:
        print("no GITLAB_TOKEN, drawing from the cache only", file=sys.stderr)

    if not counts:
        sys.exit("nothing to draw: no cache and no token")

    with open(a.out, "w") as f:
        f.write(render(counts, a.weeks, steps, a.label))
    print(f"wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
