#!/usr/bin/env sh
# This script propagates the exit code from the underlying command so it can be used with && in POSIX shells
cd "$(dirname "$0")/.." || exit 1
python -m 'scripts.update_dependencies' "$@"
