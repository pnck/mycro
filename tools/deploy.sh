#!/bin/sh
# Thin compatibility wrapper for tools/deploy.py
exec python3 "$(dirname "$0")/deploy.py" "$@"
