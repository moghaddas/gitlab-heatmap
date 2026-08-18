# My GitHub graph was empty, and it was lying

All my work happens on a private GitLab. My GitHub profile showed an empty
square, which reads as "this person does not write code".

The obvious fix is a GitHub Action that draws the GitLab history as an SVG and
commits it to the profile repository. A few of those exist. They all reach for
the same endpoint, `GET /events?action=pushed`, because it hands you one row
per push with a date on it.

That endpoint measures the wrong thing. A push carrying twelve commits is one
event, so a day of real work turns into one pale square. You can read
`push_data.commit_count` instead, but that counts what moved with the push, so
merging someone else's branch shows up as your work. GitLab also prunes old
events, which puts a floor under how far back you can go.

Commits are the thing being counted, so I read the commits endpoint,
`/projects/:id/repository/commits`, and filtered on my author address.

Then the graph came back with 26,282 commits over 109 days, and one Tuesday
holding 10,670 of them. I had asked for `all=true`, which returns one copy of a
commit for every branch that reaches it. Drop it, read the default branch only,
which is what GitHub counts anyway, and the number lands at 12,484 across 1,500
days.

Two more things earned their place. The history is cached in the repository, and
the last three weeks get refetched and replace what was cached for those days,
so a rebase leaves the graph instead of living there forever. And an expired
token now stops the run. Skipping the failure would have deleted three weeks,
put nothing back, and reported success.

It is one Python file, standard library only, and it works against a
self-hosted instance.

https://github.com/moghaddas/gitlab-heatmap
