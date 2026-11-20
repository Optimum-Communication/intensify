# Intensify Cloud

Everything in Intensify works locally. Intensify Cloud adds what one repository can't do alone:

- one graph across all of a project's repositories, with cross-repository edges resolved;
- blast radius across repositories;
- nodes planned in the browser, pulled into each repository's `intensify.yaml`.

**What leaves your machine:** only the contents of `intensify.yaml` and its fragment files, plus the commit SHA you push from. Source code is never sent. The complete contract is [`spec/sync-api/v1/openapi.yaml`](../spec/sync-api/v1/openapi.yaml).

## Log in

```sh
intensify login --api-url https://your-intensify-cloud
```

This uses the OAuth device flow (RFC 8628). The CLI prints a short code and opens your browser; you sign in there and confirm that the code matches. The token is stored in `~/.config/intensify/credentials.json` (or under `$XDG_CONFIG_HOME`), readable only by you.

```sh
intensify whoami    # who you are, and your organizations
intensify logout    # revoke the token and forget it
```

The API URL is taken from `--api-url`, then `INTENSIFY_API_URL`, then the repository's link (see below), then your only stored login.

## Link a repository

A cloud **project** groups the repositories of one system. Each repository is identified by its spec's `project.id`, which is also the prefix of cross-repository references (`auth-service:auth.login`).

```sh
intensify project create shop    # create the project and link this repository
intensify project link ana/shop  # or link this repository to an existing project
intensify project status         # repositories, versions, and pending cloud changes
```

Linking writes `.intensify/cloud.json`, which holds the API URL, organization and project and no secrets. Commit it, so your team and CI use the same project.

## Push

```sh
intensify sync push
```

This uploads the spec as one document, with fragment files merged in, and keeps the original files for history. Each push that changes something becomes a new version, and identical pushes are skipped. The CLI warns if the spec has uncommitted changes, and lists cross-repository references the project can't resolve yet, for example because the other repository hasn't been pushed.

### From CI

Create a project token and store it as the `INTENSIFY_TOKEN` secret:

```sh
intensify project token "GitHub Actions"
```

```yaml
- run: uv tool install intensify && intensify sync push
  env:
    INTENSIFY_TOKEN: ${{ secrets.INTENSIFY_TOKEN }}
```

A project token can push to and pull from its own project's repositories, and nothing else.

## Pull

```sh
intensify sync pull            # merge nodes planned in the cloud
intensify sync pull --dry-run  # show what would change
intensify sync pull --theirs   # resolve conflicts in the cloud's favor
```

Nodes and edges planned in the cloud arrive with `provenance: cloud` and `lifecycle_state: planned`. Pull is a three-way merge per node, between what you last pulled (kept in `.intensify/cloud-base.json`, so commit it), your spec, and the cloud:

| Cloud | Local | Result |
|---|---|---|
| new node | absent | added (parents before children) |
| changed | unchanged since last pull | updated |
| changed | changed differently | **conflict**: your version is kept |
| withdrawn | unchanged since last pull | removed |
| withdrawn | changed | **conflict**: your version is kept |
| any | you deleted it | your deletion is kept |

Conflicts are reported and make the command exit with 1, and they stay until you resolve them. Edit the node, or pull with `--theirs`. If the merged spec would be invalid, for example because a planned node's parent is missing, nothing is written.

## Self-hosting

The Intensify Cloud API runs anywhere Node.js and Postgres do. It signs users in through any OpenID Connect provider (WorkOS, Auth0, Amazon Cognito, Okta, Keycloak, ...) and stores raw snapshots on S3 or a local disk.
