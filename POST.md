# Drawing my GitLab commits on my GitHub profile

All my work happens on a private GitLab. My GitHub profile showed an empty
grid, which reads as "this person does not write code".

The fix is a GitHub Action that draws the GitLab history as an SVG and commits
it to the profile repository. A few exist. They all reach for
`GET /events?action=pushed`, which hands you one row per push with a date on
it.

That endpoint measures the wrong thing. Twelve commits in a single push is one
event, so a day of real work turns into a pale square. `push_data.commit_count`
counts what moved rather than what you wrote, so merging someone else's branch
shows up as your work. GitLab also prunes old events, which puts a floor under
how far back you can go.

Commits are the thing being counted, so I read
`/projects/:id/repository/commits` and filtered on my author address.

Then the graph came back with 26,282 commits over 109 days, one Tuesday holding
10,670 of them. The cause was `all=true`, which returns one copy of a commit
per branch that reaches it. Reading the default branch only, as GitHub does,
lands the number at 12,484 across 1,500 days.

Two more things earned their place. The history sits in a JSON file in the
repository, and the last three weeks are refetched every morning, replacing
what was there, so a rebase leaves the graph instead of living in it forever.
An expired token stops the run. Skipping that failure would have deleted the
window, put nothing back, and reported success.

It is one Python file, standard library only, and it works against a
self-hosted instance.

https://github.com/moghaddas/gitlab-heatmap
