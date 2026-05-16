#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install Python packages
pip install -r requirements.txt

# 2. Tell Playwright where its home folder is 
export PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright

# 3. Install Chromium browser only (without system dependencies)
python -m playwright install chromium