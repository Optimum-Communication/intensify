# CI integrations

Run [`intensify drift`](../docs/drift.md) on pull requests so code can't drift from `intensify.yaml` unnoticed.

## GitHub Action

```yaml
name: Intensify
on: pull_request

permissions:
  contents: read
  security-events: write   # code scanning annotations
  pull-requests: write     # only needed with `comment: true`

jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: Optimum-Communication/intensify/action@main
        with:
          comment: true
```

The action:

1. Installs the CLI with uv.
2. Fetches the pull request's base branch and runs `intensify drift --base`, so only drift introduced by the pull request is reported.
3. Writes a Markdown summary to the job summary.
4. Uploads findings to code scanning, where they show as annotations on the diff.
5. Optionally posts the summary as a pull request comment, and updates that same comment on later pushes.
6. Fails the job if blocking drift was found.

| Input | Default | Description |
|---|---|---|
| `path` | `.` | Spec file, or the directory containing `intensify.yaml`. |
| `package` | `intensify` | pip requirement for the CLI: a version pin (`intensify==0.1.0`), a git URL, or `.` for the checked-out repository. |
| `base` | the PR's base branch | Branch to compare with. On `push` events there's no base, so all drift is reported. |
| `upload-sarif` | `true` | Upload to code scanning. Needs `security-events: write`. Private repositories need GitHub Advanced Security. |
| `comment` | `false` | Post or update a pull request comment. Needs `pull-requests: write`. |
| `fail` | `true` | Fail the step on blocking drift. Set to `false` to only report. |
| `token` | `github.token` | Token used for the comment. |

| Output | Description |
|---|---|
| `exit-code` | `0` no blocking drift, `1` blocking drift, `2` the check couldn't run. |
| `sarif` | Path of the SARIF report. |
| `markdown` | Path of the Markdown summary. |

The shell logic lives in `drift.sh` and `comment.sh`, so it can be run and tested outside Actions.

## GitLab CI

```yaml
include:
  - remote: https://raw.githubusercontent.com/Optimum-Communication/intensify/main/action/gitlab/intensify-drift.gitlab-ci.yml
```

This adds an `intensify-drift` job to merge request pipelines. Findings appear in the merge request's Code Quality widget, and the Markdown summary is kept as an artifact. Set the `INTENSIFY_PACKAGE` and `INTENSIFY_PATH` variables to customize it.
