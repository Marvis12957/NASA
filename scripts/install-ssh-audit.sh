#!/bin/bash
# install-ssh-audit.sh — staged installer for host-level SSH auditing.
#
# REFUSES to run unless DISCOVERY.md exists next to this script (created by
# the on-server Discovery phase). Default mode is --dry-run: it prints the
# planned steps and the backups it would take, and changes nothing.
#
# Real application requires: root/sudo on the target host, a tested
# rollback, and a second open admin session (see docs/SSH_AUDIT.md §D).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=1
[ "${1:-}" = "--apply" ] && DRY_RUN=0

if [ ! -f "$DIR/DISCOVERY.md" ]; then
    echo "REFUSED: DISCOVERY.md not found next to this script ($DIR)."
    echo "Run the Discovery phase on the server first (spec section 2),"
    echo "write its findings to DISCOVERY.md, then re-run this installer."
    exit 2
fi

echo "Discovery present: $DIR/DISCOVERY.md"
echo "Planned steps (from docs/SSH_AUDIT.md):"
echo "  1. Back up /etc/ssh/sshd_config, auditd rules, journald config"
echo "     -> /root/agent-platform-backup-<date>/ (printed before applying)"
echo "  2. Install auditd execve rules for sshd sessions (per Discovery)"
echo "  3. Configure journald persistence + sshd LogLevel VERBOSE"
echo "  4. Install session-recording config for the discovered SSH mechanism"
echo "  5. Create /var/log/agent-platform/ (append-only, restricted read)"
echo "  6. Syntax-check everything (sshd -t, auditctl -R); restart auditd only"
echo "  7. Run host-level SSH tests with an isolated test account"
echo "  8. FAIL-CLOSED enforcement ONLY after the fail-closed test passes"
echo "     and a second admin session is open."
if [ "$DRY_RUN" = 1 ]; then
    echo ""
    echo "DRY RUN — nothing was changed. Re-run with --apply to proceed."
    exit 0
fi
echo ""
echo "ERROR: --apply is intentionally not implemented in this staging package."
echo "The installer must be finalised against the real Discovery findings on"
echo "the server (SSH mechanism, existing audit services, Tailscale policy)."
echo "See docs/SSH_AUDIT.md."
exit 3
