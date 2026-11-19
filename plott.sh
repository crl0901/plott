#!/usr/bin/env bash
# Plott: create and run job boards with Docker Compose.
#
#   ./plott.sh setup                    create a board (asks questions, writes boards/NAME/)
#   ./plott.sh NAME up -d --build       start the board
#   ./plott.sh NAME logs -f             follow its logs
#   ./plott.sh NAME down                stop it
#
# Each board lives in boards/NAME/: .env, resume.md, config/, and its data.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

usage="usage: ./plott.sh setup | ./plott.sh NAME [docker compose args...]"
if [[ "${1:-}" == "setup" ]]; then
  shift
  command -v python3 >/dev/null || { echo "Setup needs Python 3 (python3)." >&2; exit 1; }
  exec python3 -I tools/setup_board.py "$@"
fi

name="${1:?$usage}"
shift
[[ "$name" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "Board names use lowercase letters, numbers, - and _." >&2; exit 1; }
dir="./boards/$name"
[[ -f "$dir/.env" ]] || { echo "There is no board named '$name'. Create one with: ./plott.sh setup" >&2; exit 1; }
[[ -f "$dir/resume.md" ]] || { echo "Missing $dir/resume.md. Put your resume there as Markdown." >&2; exit 1; }

# Create the folders the board writes to, owned by you. The container runs as your user ID so it
# can write to them (root is mapped to 1000, the image's default).
mkdir -p "$dir/data" "$dir/resumes" "$dir/cover-letters"
uid="$(id -u)"
[[ "$uid" == 0 ]] && uid=1000
APP_UID="$uid" BOARD_DIR="$dir" exec docker compose -p "plott-$name" --env-file "$dir/.env" "$@"
