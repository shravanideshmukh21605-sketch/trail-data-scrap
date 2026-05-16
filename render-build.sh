#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install Python packages
pip install -r requirements.txt

# 2. Force Playwright to download ONLY the headless binary (bypasses sudo prompts)
python -m playwright install chromium-headless-shell