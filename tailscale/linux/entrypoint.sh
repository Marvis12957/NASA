#!/bin/bash
set -euo pipefail

USER_NAME="${SSH_USER:-coder}"
PASSWORD="${SSH_PASSWORD:-}"

if [[ -z "${PASSWORD}" ]]; then
  echo "SSH_PASSWORD is empty. Set it in docker/linux/.env" >&2
  exit 1
fi

if ! id "${USER_NAME}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --user-group --groups sudo "${USER_NAME}"
fi

echo "${USER_NAME}:${PASSWORD}" | chpasswd
echo "${USER_NAME} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${USER_NAME}"
chmod 440 "/etc/sudoers.d/${USER_NAME}"

HOME_DIR="$(getent passwd "${USER_NAME}" | cut -d: -f6)"
install -d -m 700 -o "${USER_NAME}" -g "${USER_NAME}" "${HOME_DIR}/.ssh"

if [[ -s /authorized_keys/authorized_keys ]]; then
  install -m 600 -o "${USER_NAME}" -g "${USER_NAME}" \
    /authorized_keys/authorized_keys "${HOME_DIR}/.ssh/authorized_keys"
fi

# Workspace is mounted here. Keep a stable path in the home directory.
if [[ -d /workspace && ! -e "${HOME_DIR}/NASA" ]]; then
  ln -s /workspace "${HOME_DIR}/NASA"
  chown -h "${USER_NAME}:${USER_NAME}" "${HOME_DIR}/NASA"
fi

ssh-keygen -A
exec /usr/sbin/sshd -D -e
