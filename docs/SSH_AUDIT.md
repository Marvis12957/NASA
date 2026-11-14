# SSH Audit — DESIGN (not yet deployed)

> **STATUS: DESIGN ONLY.** The final configuration depends on the Discovery
> phase, which must run on the target server first (see spec §2: identify
> the SSH mechanism — Tailscale SSH built-in server, OpenSSH over the
> Tailscale network, Tailscale PAM, or a mix — plus existing audit
> services, users, and policies). **Nothing in this document has been
> deployed or tested on the host.** `scripts/install-ssh-audit.sh` refuses
> to run until a `DISCOVERY.md` exists next to it.

## Goals

Auditable records for **every** SSH access path into the development
server: connection metadata, executed commands (to the extent observable),
and the correlation chain to agent tasks and git changes. Logging only
inside `agentctl` is insufficient.

## A. Connection logging

For every session, capture:

| Field | Source (candidate) |
|---|---|
| connection time / disconnection time | sshd / journald / Tailscale SSH logs |
| authenticated Linux user | sshd auth log |
| Tailscale identity (where reliably exposed) | Tailscale SSH / control-plane logs |
| source device / address | sshd `sshd[...]: Accepted ... from ...` |
| SSH session identifier | sshd session id / systemd unit |
| authentication result | sshd auth log |
| session duration | derived from connect/disconnect pair |

Do **not** fabricate identity information the mechanism does not expose.

## B. Command and execution auditing

Evaluate in this order during Discovery: `auditd` (syscall-level execve
auditing, most complete for non-interactive commands), systemd journal
(unit-scoped session logs), a controlled agent launcher (wrapper that
logs argv/cwd/exit status per agent run), and SSH session recording.

Record where supported: command + arguments, PID, PPID, effective user,
working directory, timestamp, exit status, execution session id, agent
run / task association (via `AGENT_RUN_ID` / `SSH_SESSION_ID` env).

Terminal recording alone does **not** cover background processes, all
commands, or filesystem changes. **Shell history alone is NOT an
acceptable audit solution.**

## C. Access-path coverage matrix

| Path | Mechanism (to confirm in Discovery) | Expected coverage |
|---|---|---|
| Interactive SSH | session recorder / auditd | full session + execve |
| `ssh host command` | auditd execve | command + args |
| SCP / SFTP | sshd subsystem logging + auditd | file transfer events |
| Port forwarding | sshd logs | forward open/close |
| Background processes | auditd | execve regardless of tty |
| tmux / screen | auditd + session recorder | execve; terminal scrollback only if recorded |
| sudo / privilege escalation | sudo logs + auditd | full |
| Direct access without agentctl | auditd (host-level) | **this is why host-level audit is mandatory** |

## D. Fail-closed behaviour

- For normal developer access: if the mandatory recording service is
  unavailable, new unrecorded sessions/agent executions must be refused.
- Preserve a **separately controlled administrator recovery channel**
  (different auth path, documented, tested) so admins are never locked out.
- **Test fail-closed behaviour before enforcing it globally.** Never
  enforce by editing the live SSH config without a tested rollback and a
  second open admin session.

## E. Audit protection

- Logs live under `/var/log/agent-platform/` (or the host equivalent):
  `ssh-events.jsonl`, `agent-events.jsonl`, `task-events.jsonl`,
  `git-events.jsonl`, `recovery-events.jsonl`.
- Append-only; ordinary developers and coding agents cannot modify or
  delete historical events (separate owner / privileged collector writes).
- Log rotation + retention controls; forward to storage outside agent
  write permissions.
- Restricted read access; no plaintext secrets in logs.

## Honest coverage limits

- Commands executed through a mechanism auditd cannot see (e.g. a
  compromised binary evading syscall auditing) are out of scope.
- Tailscale SSH vs OpenSSH differences decide what identity fields exist;
  do not claim fields Discovery did not confirm.
- Session recording may miss output written directly to `/dev/pts` by a
  different process; auditd still sees the execve.
- **Do not claim absolute recording coverage unless tests substantiate
  it.** The test suite includes host-level SSH tests to run after
  deployment (isolated test account, preserving admin access).
