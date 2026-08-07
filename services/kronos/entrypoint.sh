#!/usr/bin/env sh
set -eu

cache_dir="${HF_HOME:-/models/kronos-huggingface}"
mkdir -p "$cache_dir"
chown -R foundation:foundation "$cache_dir"
exec gosu foundation "$@"
