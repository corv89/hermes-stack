# Container ↔ Host Path Mapping

## The problem

Hermes runs inside the webui container. When you produce an artifact that the
user needs to install or access from the host, you MUST give the host-side path.
Container paths are invisible to the user.

## Key mappings

| Container path | Host path | Mount source |
|----------------|-----------|--------------|
| `/workspace` | `~/Src/hermes-pod/hermes-workspace/` | quadlet `{{REPO}}/hermes-workspace` |
| `/work` | `~/Src/` (or `$PROJECT_MOUNT`) | quadlet `{{PROJECT_MOUNT}}` |
| `~/.hermes/` | `/home/hermeswebui/.hermes/` | btrfs subvol `/home` |

Where:
- `REPO` = hermes-pod repo dir on the host (`~/Src/hermes-pod/`), set in `run.py`
- `PROJECT_MOUNT` defaults to `~/Src/` (env var override)

## How to trace a mount

When unsure of a container→host path mapping:

```bash
# Check /proc/mounts for the container path
grep workspace /proc/mounts

# Or read the quadlet templates directly
grep -n 'workspace\|Volume' /work/hermes-pod/quadlet/hermes-webui.container
grep -n 'workspace\|Volume' /work/hermes-pod/quadlet/hermes-opencode.container

# Then resolve template variables (REPO, PROJECT_MOUNT)
grep -n 'REPO\|PROJECT_MOUNT' /work/hermes-pod/run.py
```

## Common scenario

You write a file to `/workspace/oc-v4.py` and need to tell the user how to
install it. The WRONG instruction is:

```
sudo cp /workspace/oc-v4.py /usr/local/bin/oc
```

The RIGHT instruction is:

```
sudo cp ~/Src/hermes-pod/hermes-workspace/oc-v4.py /usr/local/bin/oc
```

## Shared ownership split

- `/workspace`: Hermes (webui) owns read-write; OpenCode sees read-only.
- `/work`: OpenCode owns read-write; Hermes sees read-only.
- `~/.hermes/`: Hermes owns read-write; not visible to OpenCode container.

This split is the safety boundary. See opencode-driver skill "Delegation
discipline" for why `/work` is read-only for Hermes.
