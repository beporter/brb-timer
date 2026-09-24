#!/usr/bin/env bash
# Kick off the full unit test suite consistently.

set -euo pipefail

if grep -qv '^coverage ' <(pipx list --short | cat); then
    pipx install coverage
fi

# All of the below use .coveragerc as their config file.
coverage run
coverage report
coverage html
#open tmp/coverage/index.html
