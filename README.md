# gitlab-heatmap

Your commits are on GitLab. Your GitHub profile shows an empty square. This
draws the real graph and commits it to your profile repository every morning.

![Example heatmap](example.svg)

It counts commits, one square per day, in the same shades GitHub uses, in both
light and dark mode.

## Use it

Put this in `.github/workflows/gitlab-heatmap.yml` in your profile repository,
the one named after your username:

```yaml
name: GitLab heatmap

on:
  schedule:
    - cron: "17 4 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  draw:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: moghaddas/gitlab-heatmap@v1
        with:
          gitlab-token: ${{ secrets.GITLAB_READ_TOKEN }}
```

Then reference the SVG from your `README.md`:

```markdown
![GitLab contributions](gitlab-heatmap.svg)
```

`GITLAB_READ_TOKEN` is a GitLab personal access token carrying `read_api` and
nothing else. Do not use a token with `api`. Anyone who can add a workflow to
the repository can read the secret, and `read_api` only gets them your commit
history.

## Why not the events API

This is the first thing to reach for, and every other tool in this space uses
it. `GET /events?action=pushed` gives you one row per push, ready to plot.

It measures the wrong thing. One push carrying twelve commits is one event, so
a day of real work shows as a single light square. You can read
`push_data.commit_count` instead, but that counts what moved with the push
rather than what you wrote, so merging a colleague's branch lands on your
graph. GitLab also prunes old events, which caps how far back you can go.

This reads `/projects/:id/repository/commits` instead. One row per commit,
filtered to your author address, kept for as long as the repository exists.

## What it gets right

**Default branch only.** Asking for `all=true` returns one copy of a commit per
branch that reaches it. On my own history that turned 12,484 commits into
26,282, with 10,670 of them landing on a single day. Commit ids are also
tracked across projects, so a fork or a mirror cannot double-count.

**Several addresses.** Git commits carry whichever address was configured at
the time, and most people own three or four across the years. Leave
`identities` empty and it reads them from the token owner, including the
GitLab noreply address. Set it when you have an old address to add.

**A cache it can correct.** The history lives in
`data/gitlab-contributions.json`, committed next to the SVG, so a normal
morning costs a handful of API reads instead of a walk over every project. The
last three weeks are refetched and replace what was cached for those days, so a
rebase drops out of the graph rather than staying there forever.

**A run that fails loudly.** An expired token answers 401 on every project. A
tool that skips failures would delete the refetched window, put nothing back,
and report success while three weeks quietly vanished. This stops instead.

## The first run

The daily schedule only refreshes recent days. Fill in the history once:

```
gh workflow run gitlab-heatmap.yml -f backfill=true
```

Expect a few minutes on the first pass, and seconds after that.

## Options

| Input | Default | |
|---|---|---|
| `gitlab-token` | required | Token with `read_api` |
| `gitlab-url` | `https://gitlab.com` | Base URL of a self-hosted instance |
| `identities` | from the token owner | Comma-separated author addresses |
| `out` | `gitlab-heatmap.svg` | Where to write the SVG |
| `cache` | `data/gitlab-contributions.json` | Where to keep the history |
| `backfill` | `false` | Walk everything instead of the window |
| `since` | `2015-01-01` | Backfill start date |
| `window` | `21` | Days to refresh on a normal run |
| `weeks` | `53` | Columns to draw, up to 53 |
| `steps` | `0,2,5,10` | Upper bound of each shade |
| `label` | `{total} commits on GitLab in the last year` | Caption |
| `commit` | `true` | Commit and push when the graph changes |
| `commit-message` | `Update the GitLab heatmap` | |
| `commit-user-name` | `github-actions[bot]` | |
| `commit-user-email` | the bot noreply address | |

Set `commit-user-email` to your own GitHub noreply address if you want these
commits to count on your GitHub graph too. Some people want that, some find it
dishonest. It is off by default.

## Run it yourself

The script is one file and imports only the standard library.

```
export GITLAB_TOKEN=glpat-...
python3 gitlab_heatmap.py --backfill
python3 gitlab_heatmap.py
python3 test_heatmap.py
```

Every flag in the table above works on the command line, spelled the same way
with two dashes.

## Limits

- **Private projects need the token, public ones do not.** The graph shows
  every project the token can read, so a token from a work account draws your
  work history. That is usually the point, but check what you are exposing:
  the SVG carries daily commit counts, and someone can read your holidays off
  it.
- **Membership only.** It walks the projects you are a member of. A commit you
  authored in a repository you have since been removed from is invisible.
- **Committer date, not author date.** A rebase moves a commit to the day it
  was replayed. GitHub has the same behaviour.
- **One address per commit.** Co-authored trailers are not parsed.

## License

MIT. See [LICENSE](LICENSE).
