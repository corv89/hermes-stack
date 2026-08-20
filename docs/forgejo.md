# Forgejo (git forge)

Self-hosted [Forgejo] (v15 LTS) for repos, PRs, and Forgejo Actions CI with a
local runner on the host. GitHub stays as a **push-mirror backup** (configured
per-repo after install, not automated). The web installer is disabled
(`INSTALL_LOCK`), so `run.py` bootstraps the admin account and registers the
Actions runner automatically (`forgejo_bootstrap`, idempotent,
warn-and-continue like every add-on).

The runner itself is the `quadlet/hermes-forgejo-runner.container` unit:
`run.py` writes its config (`/opt/forgejo-runner/config/runner-config.yml`,
mode `0600`) and starts the unit after registration. Jobs run through the
host's rootless podman socket (Docker-compatible API).

## One-time host prep

Data dirs owned by you, plus the rootless podman socket the runner drives jobs
through (Docker-compatible API):

```bash
sudo mkdir -p /opt/forgejo-data /opt/forgejo-runner/config /opt/forgejo-runner/data
sudo chown "$USER": /opt/forgejo-data /opt/forgejo-runner /opt/forgejo-runner/config /opt/forgejo-runner/data
systemctl --user enable --now podman.socket   # runner's Docker-compatible endpoint
```

## `.env` keys

`FORGEJO_ADMIN_USER`, `FORGEJO_ADMIN_EMAIL`, `FORGEJO_ADMIN_PASSWORD` are
required (the web installer is off, so the admin account can only come from
`run.py`). Optional `FORGEJO_ROOT_URL` for public links/emails once exposed via
Tailscale.

**Access**: http://127.0.0.1:3000. Tailnet exposure: `tailscale serve --bg 3000`,
then set `FORGEJO_ROOT_URL` to the https URL and re-run `python3 run.py`.

## GitHub backup mirror

Per repo, one-time: create the repo on GitHub, create a PAT (classic, `repo`
scope), then in Forgejo: Settings -> Repository -> Mirror Settings -> push
mirror `https://github.com/<user>/<repo>.git`, username + PAT, enable "Sync
when new commits are pushed". API equivalent:
`POST /api/v1/repos/{owner}/{repo}/push_mirrors`.

> **Warning:** the push mirror **force-pushes**. GitHub is the backup, never
> the primary. Once mirroring is on, never push to GitHub directly, or a
> mirror sync will clobber divergent GitHub-side commits.

## Migrating a repo from GitHub

Make Forgejo the primary-of-record: `git clone --mirror` on the host, push the
mirror to Forgejo, then add the push mirror back to GitHub, then flip your
local clones' `origin` to Forgejo.

## Porting workflows

Copy `.github/workflows/` to `.forgejo/workflows/`. Forgejo Actions is familiar,
not byte-compatible (runner v13 is stricter: no `set-output`/`add-path`,
invalid matrices fail hard). `DEFAULT_ACTIONS_URL` points at GitHub, so ported
workflows keep using `uses: actions/checkout@v4` unchanged.

## Backup/restore

Git data is covered by the GitHub mirror; for a full instance snapshot:

```bash
podman exec hermes-forgejo forgejo dump -f /data/forgejo-dump.zip && \
  podman cp hermes-forgejo:/data/forgejo-dump.zip .
```

## Adding the arm64 runner later

CM5 or another tailnet box: install the `forgejo-runner` binary (or the same
container image) there, register it offline from the host with
`forgejo forgejo-cli actions register --name <n> --scope '' --secret <40hex>`,
and point its config at the tailnet URL of the forge.

## Upgrades

Bump the image tag. Patch-level `:15` tag bumps are safe: `podman pull` +
`python3 run.py --redeploy`. Major upgrades (X to X+1) require reading the
release notes for manual steps first.

[Forgejo]: https://forgejo.org
