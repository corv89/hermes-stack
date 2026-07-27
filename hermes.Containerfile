FROM ghcr.io/nesquena/hermes-webui:latest

LABEL maintainer="corv"
LABEL description="Hermes WebUI + Hermes Agent + opencode2 thin client"

USER root

# Install Hermes Agent (non-interactive, skip setup wizard)
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup

# Symlink the agent into a path the webui init already searches.
RUN ln -s /usr/local/lib/hermes-agent /opt/hermes

# Install opencode2 CLI (thin client for driving the sibling opencode container)
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @opencode-ai/cli@0.0.0-next-15919 \
    && rm -rf /var/lib/apt/lists/*

# Patch the startup init: prune ~/.hermes/bin from the chown walk.
# The hermes-agent installs `tirith` into ~/.hermes/bin with a filesystem
# lock that makes even root chown/rm fail with "Permission denied". Without
# this prune, the init's chown walk aborts on tirith and kills startup.
RUN python3 <<'PYEOF'
from pathlib import Path
p = Path("/hermeswebui_init.bash")
c = p.read_text()
old = '    -path "/home/hermeswebui/.hermes/hermes-agent" -prune \\'
add = '\n    -o -path "/home/hermeswebui/.hermes/bin" -prune \\'
if old in c:
    p.write_text(c.replace(old, old + add, 1))
    print("patched: ~/.hermes/bin pruned from chown walk")
else:
    print("WARNING: could not find hermes-agent prune line")
PYEOF

# Bake the opencode-driver skill into a dedicated root-owned dir, registered
# via skills.external_dirs in config.yaml (see run.sh). Root-owned so Hermes
# (uid 501) can't modify/delete it; discovered by the agent via external_dirs.
# Update by editing the host file and rebuilding the image.
COPY opencode-skill/opencode-driver/SKILL.md \
     /opt/hermes-skills/opencode-driver/SKILL.md

# Bake the web-research skill (SearXNG/Trafilatura/Playwright search+extract flow).
COPY skills/web-research/SKILL.md \
     /opt/hermes-skills/web-research/SKILL.md

# Bake the `oc` wrapper so Hermes can drive OpenCode with a one-liner.
COPY --chmod=755 bin/oc /usr/local/bin/oc

# Bake `ocm` so Hermes can switch the session model/agent via the API (the TUI's
# Ctrl+X M model picker does not render through the PTY bridge).
COPY --chmod=755 bin/ocm /usr/local/bin/ocm

CMD ["/hermeswebui_init.bash"]
