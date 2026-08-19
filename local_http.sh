#!/usr/bin/env bash
# Start a simple local webserver to catch OAuth redirects during testing.

set -euo pipefail

{
    cd pages
    python3 -m http.server 8765
}
