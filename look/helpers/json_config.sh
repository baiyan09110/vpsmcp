#!/bin/bash
# vps-look helper: print the configured JSON config file. Redaction happens
# in the MCP layer (values under keys named *key*/*token*/*secret*/
# *password* are replaced before anything leaves the box).
set -u
path="${VPSLOOK_JSON_CONFIG:-}"
if [ -z "$path" ]; then
  echo "ERROR: json_config not configured" >&2; exit 2
fi
cat "$path"
