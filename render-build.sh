#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install Python packages
pip install -r requirements.txt

# 2. Tell Playwright to build directly into the project folder root
export PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright

# 3. Force Playwright to download only the headless binary
python -m playwright install chromium-headless-shell