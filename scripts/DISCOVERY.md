# DISCOVERY.md — nasa-linux (run 2026-11-14, read-only)

Discovery was executed over SSH on the live server **before any change**
(spec §2). Raw output: 177 lines, captured from `/tmp/discovery.sh`.

## 1. OS, init, filesystem

- Ubuntu 24.04.5 LTS, kernel `7.0.14-linuxkit`, **aarch64** — a Docker
  container (`/.dockerenv` present), not a bare-metal/VM install.
- **PID 1 is `sshd` itself** (`/usr/sbin/sshd -D -e`). **No systemd, no
  journald, no systemctl.** Restarting sshd = restarting the container's
  main process — treated as disruptive, never done without admin approval.
- Root filesystem: overlay, 911G, 863G free.
- `/workspace` is a **bind mount of a host (Mac) folder**
  (`/run/host_mark/Users`, "fakeowner") shared into two containers. All
  developers/agents reach it through this one container, so `flock` and
  atomic rename are arbitrated by this container's kernel.

## 2. Users, groups, sudo

- Linux users: `root`, `ubuntu` (uid 1000, unused for dev), **`coder`
  (uid 1001)** — the only account used for development SSH.
- `coder` is in group `sudo` with **NOPASSWD: ALL** (created by the
  container entrypoint: `/etc/sudoers.d/coder`).
- **Finding F1 (high impact): every developer shares the single Linux
  user `coder`.** Per-developer attribution at OS level is impossible
  today; the platform therefore attributes work by `agent_id` +
  `agent_run_id` in `agentctl` events, and host-level per-person audit
  requires per-developer accounts (phase 2, admin decision).
- **Finding F2: `coder` has unrestricted passwordless sudo.** Any audit
  log writable by `coder` is also erasable by anyone with the shared
  password + sudo. Tamper-resistance inside this container is
  best-effort only; real protection needs host-level log forwarding
  (outside this container) — documented gap, not silently ignored.

## 3. SSH mechanism (spec §2.3)

- **OpenSSH over the Tailscale network.** NOT Tailscale SSH (no
  tailscale SSH server, no `pam_tailscale`), NOT Tailscale PAM.
- Topology: Tailscale **sidecar container** (`tailscale/tailscale:stable`,
  userspace networking, `TS_SERVE_CONFIG`) forwards TCP 22 →
  `linux:22` (see `tailscale/linux/tailscale/serve.json`,
  `docker-compose.yml`). The dev container itself has **no tailscale
  binary** and no tailnet interface.
- sshd config (effective lines found): `PermitRootLogin no`,
  `PasswordAuthentication yes`, `PubkeyAuthentication yes`,
  `Subsystem sftp /usr/lib/openssh/sftp-server`. sshd runs with `-e`
  (logs to stderr → captured by Docker on the **host**, outside this
  container; not readable from inside).
- Entrypoint (`tailscale/linux/entrypoint.sh`) creates user from
  `SSH_USER`/`SSH_PASSWORD` env (`.env` file), installs
  `/authorized_keys/authorized_keys` if present, symlinks `~/NASA ->
  /workspace`, then `exec sshd -D -e`.
- **Finding F3: source identity is lost at the serve forward.** Inside
  the container, `SSH_CONNECTION` shows the docker bridge address of the
  Tailscale container (`172.18.0.3 → 172.18.0.2:22`) for every session.
  sshd cannot see the real client device, and Tailscale identity is not
  exposed to it. Connection logs inside the container therefore cannot
  attribute a session to a person/device — only the host-side Docker log
  of the Tailscale container + the Tailscale admin panel see more.
- **Finding F4: alternative entry point — published host port.**
  `docker-compose.yml` publishes `2222:22` on the host, bypassing
  Tailscale entirely (reachable wherever the host's 2222 is reachable).
  Any audit design must count this path; closing it is a host/compose
  change = admin decision, not done here.

## 4. Tailscale policies / audit capabilities

- The node `nasa-linux` (100.77.128.17) is a **device shared into the
  operator's tailnet** from the owner's tailnet. From inside the
  container there is no Tailscale API/CLI access; tailnet policy
  (ACLs, SSH policies) is administered by the machine owner's Tailscale
  admin — **not modified** (spec §13) and not inspectable from here.

## 5. Git repository state (at discovery time)

- Repo `/workspace`, branch `main`, remote
  `origin https://github.com/Marvis12957/NASA.git`, HEAD `08f1a42
  "creat project"` on top of `5744c06` initial commit.
- **Active unrelated development in progress**: `backend/` GraphRAG app
  with many uncommitted files + modified `README.md`. Platform deploy
  must not touch, stage, or commit any of it. Verified by path-scoped
  `git add` only.
- Team tests are pinned by `pytest.ini` (`testpaths = backend/tests`),
  so the platform's root-level `tests/` folder is not collected by the
  team's pytest runs.
- Root `.env` holds Neo4j credentials; it is covered by `.gitignore`
  (verified: not listed by `git status`).

## 6. Existing audit services (spec §2.7)

- **auditd: absent** (no binary, no `/etc/audit`). auditd also cannot
  meaningfully run in this container (needs host kernel control).
- **journald: absent** (no systemd). **rsyslog/syslog-ng: absent.**
  No `/var/log/auth.log`. Present: `wtmp`, `btmp`, `faillog`, `lastlog`
  (login records only — not command auditing).
- **tlog/snoopy: absent.** No session recording installed.
- sshd's own logs go to the container stderr → host Docker logs
  (outside agent reach — good for tamper-resistance, but also outside
  this deployment's reach).

## 7. What this means for the SSH-audit requirement

Implemented in this deployment (inside-container, non-disruptive):

1. `agentctl` JSONL events (task/agent/git/recovery) — full correlation
   chain agent_run → task → branch/worktree → commit.
2. Login-shell session collector (`/etc/profile.d/agent-platform-audit.sh`,
   installed with sudo, reversible): appends `session_start` /
   `session_end` JSON lines for interactive login shells to
   `/var/log/agent-platform/ssh-events.jsonl` (time UTC, user,
   `SSH_CONNECTION` as seen inside the container, tty, shell PID).
   Session id is **derived and labelled as derived** — never fabricated.

Explicitly NOT claimed / remaining gaps (need admin/host action):

- No execve-level command auditing (auditd) — impossible inside this
  container; requires host-level install on the Mac/Docker host.
- Non-interactive `ssh host cmd`, SCP/SFTP transfers, port forwarding,
  and background processes are **not** covered by the profile.d
  collector. sshd logs for these exist only in host Docker logs.
- Real client device / Tailscale identity per session: not available
  inside the container (F3).
- Fail-closed enforcement: NOT enabled. It would require sshd/PAM
  changes and an sshd restart (= container main-process restart) and
  must be tested with a second admin session per spec §6/§13 — left as
  an admin-approved phase 2.
- Log tamper-resistance: limited by F1/F2 (shared user + NOPASSWD sudo).
- Host port 2222 path (F4) bypasses Tailscale; closing or auditing it
  is a host-side decision.

## 8. Safety notes for deployment

- Backups: any file the deploy modifies gets a timestamped backup under
  `/root/agent-platform-backup-*` (sudo) or `*.bak-<ts>` in place.
- Nothing in sshd, PAM, firewall, or Tailscale config is modified by
  this deployment. Rollback: `docs/ROLLBACK.md`.
