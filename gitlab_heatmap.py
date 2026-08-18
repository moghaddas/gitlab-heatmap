#!/usr/bin/env python3
"""Draw a GitHub-style contribution heatmap from GitLab commit history.

Counts commits, not push events. The events API reports one row per push, so
ten commits pushed together show as a single contribution, and old rows are
pruned. This reads /projects/:id/repository/commits instead, which keeps every
commit for as long as the repository exists.

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
MAX_SHADES = 4


class Fatal(Exception):
    """An error that must stop the run instead of skipping one project."""


# --- GitLab ------------------------------------------------------------------


def get(api, path, token, **params):
    """One API read, retried on a timeout, a rate limit or a server error.

    A busy project answers slowly enough to time out on the first ask, and a
    backfill over many projects is exactly the load that earns a 429. A
    dropped page removes whole months from the history instead of failing the
    run, so this retry keeps the graph honest.
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
            # those would delete the refetched window from the cache, put
            # nothing back, and still report success.
            if e.code in (401, 403):
                raise Fatal(f"GitLab returned {e.code}. Check the token and "
                            f"its read_api scope.")
            if e.code != 429 and e.code < 500:
                raise  # 404 on an empty project is an answer, not a failure
            last = e
            pause = e.headers.get("Retry-After") if e.headers else None
            time.sleep(int(pause) if (pause or "").isdigit() else 2 ** attempt)
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


def utc_day(stamp):
    """The UTC date a commit belongs to.

    GitLab returns committed_date with the committer's own offset, so slicing
    the first ten characters buckets by whatever timezone that person sat in.
    The window prune works in UTC. A commit written late in the evening west of
    Greenwich would land on a day the prune never clears, and count twice.
    """
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


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
    and what GitHub counts. `all=true` returns one copy of a commit per branch
    that reaches it. That inflates a busy day by an order of magnitude and
    breaks paging over the duplicated set. `seen` carries commit ids across
    projects, so a fork or a mirror cannot double-count.
    """
    days, page, added = Counter(), 1, []
    while True:
        try:
            batch = get(api, f"projects/{project_id}/repository/commits", token,
                        since=since, until=until, per_page=100, page=page)
        except Fatal:
            raise
        except Exception as e:
            # An empty or archived project answers 404. That is not a failure
            # of the run. A project that dies on page 7 of 20 is different:
            # keeping those six pages would cache half a history as complete
            # and never refetch it, so this throws the partial read away.
            for cid in added:
                seen.discard(cid)
            print(f"  skipped {project_id}: {e}", file=sys.stderr)
            return Counter(), False
        if not batch:
            return days, True
        for c in batch:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            added.append(c["id"])
            if (c.get("author_email") or "").lower() in mine:
                days[utc_day(c["committed_date"])] += 1
        page += 1


def collect(api, token, since, until, mine):
    total, seen, ok, skipped = Counter(), set(), 0, 0
    found = projects(api, token)
    for pid, name in found:
        days, clean = commit_days(api, token, pid, since, until, mine, seen)
        ok, skipped = (ok + 1, skipped) if clean else (ok, skipped + 1)
        if days:
            print(f"  {name}: {sum(days.values())} commits", file=sys.stderr)
        total.update(days)
    # Whatever comes back replaces the cached copy of these days, so a run
    # that read most of GitLab badly is worse than one that did not run.
    if found and skipped > ok:
        raise Fatal(f"{skipped} of {len(found)} projects failed to read. "
                    f"Refusing to overwrite the cache with a partial result.")
    return total


# --- cache -------------------------------------------------------------------


def load_cache(path):
    try:
        with open(path) as f:
            return Counter(json.load(f))
    except FileNotFoundError:
        return Counter()
    except (json.JSONDecodeError, ValueError) as e:
        raise Fatal(f"the cache at {path} is not readable JSON ({e}). "
                    f"Delete it and run once with --backfill.")


def save_cache(path, counts):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # A run cancelled halfway through this write would leave JSON that every
    # later run refuses, so the file appears whole or not at all.
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(sorted(counts.items())), f, indent=0, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


# --- rendering ---------------------------------------------------------------

CELL, GAP, RADIUS = 11, 3, 2
TOP, LEFT = 20, 30
MIN_WIDTH = 340
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
DAY_LABELS = ((1, "Mon"), (3, "Wed"), (5, "Fri"))


def level(n, steps):
    for i, edge in enumerate(steps):
        if n <= edge:
            return i
    return len(steps)


def grid_start(today, weeks):
    """The Sunday the top-left cell sits on.

    Row 0 is Sunday, as it is on GitHub, so the Mon/Wed/Fri labels line up
    with rows 1, 3 and 5.
    """
    end = today + timedelta(days=(5 - today.weekday()) % 7)  # Saturday
    return end - timedelta(weeks=weeks - 1, days=6)


def render(counts, weeks, steps, label):
    today = date.today()
    start = grid_start(today, weeks)

    width = max(LEFT + weeks * (CELL + GAP) + 10, MIN_WIDTH)
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

    for d, name in DAY_LABELS:
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


def parse_steps(raw):
    try:
        steps = sorted({int(s) for s in csv(raw)})
    except ValueError:
        raise Fatal("--steps must be a comma-separated list of whole numbers")
    if not steps:
        raise Fatal("--steps needs at least one bound")
    # The stylesheet carries five shades. A sixth would render with no fill
    # rule at all, which paints the cell black in both themes.
    if len(steps) > MAX_SHADES:
        raise Fatal(f"--steps takes at most {MAX_SHADES} bounds, "
                    f"one per shade above empty")
    return steps


def check_label(label, weeks):
    try:
        label.format(total=0, weeks=weeks)
    except (KeyError, IndexError, ValueError) as e:
        raise Fatal(f"--label holds a placeholder this tool cannot fill ({e}). "
                    f"Only {{total}} and {{weeks}} are available.")


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

    try:
        run(a)
    except Fatal as e:
        sys.exit(f"stopping: {e}")


def run(a):
    if not 1 <= a.weeks <= 53:
        raise Fatal("--weeks must be between 1 and 53")
    if a.window < 1:
        raise Fatal("--window must be at least one day")
    steps = parse_steps(a.steps)
    check_label(a.label, a.weeks)

    api = a.url.rstrip("/") + "/api/v4"
    token = os.environ.get("GITLAB_TOKEN")
    counts = load_cache(a.cache)

    if token:
        mine = identities(api, token,
                          a.identity or csv(os.environ.get("GITLAB_IDENTITIES")))
        now = datetime.now(timezone.utc)
        # Midnight, not the current time of day. A window starting at 18:00
        # would drop every commit made earlier on the boundary day, because
        # the prune below removes the whole day and the fetch replaces only
        # the evening of it.
        first_day = (a.since if a.backfill
                     else (now.date() - timedelta(days=a.window)).isoformat())
        since = f"{first_day}T00:00:00Z"
        print(f"counting commits by {', '.join(sorted(mine))} since {first_day}",
              file=sys.stderr)
        fresh = collect(api, token, since, now.strftime("%Y-%m-%dT%H:%M:%SZ"), mine)
        # What comes back is authoritative for these days, so drop the cached
        # copy first. Otherwise a rebased or amended commit counts twice.
        for day in [d for d in counts if d >= first_day]:
            del counts[day]
        counts.update(fresh)
        save_cache(a.cache, counts)
        print(f"cached {len(counts)} active days, {sum(counts.values())} commits",
              file=sys.stderr)
    else:
        print("no GITLAB_TOKEN, drawing from the cache only", file=sys.stderr)

    if not counts:
        raise Fatal("nothing to draw: no cache and no token")

    parent = os.path.dirname(a.out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(a.out, "w") as f:
        f.write(render(counts, a.weeks, steps, a.label))
    print(f"wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
