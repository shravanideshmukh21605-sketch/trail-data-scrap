#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install Python packages
pip install -r requirements.txt

# 2. Set the global cache directory pathway
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/.cache/ms-playwright

# 3. Download the headless binary into that directory
python -m playwright install chromium-headless-shell