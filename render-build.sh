#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Install Python packages
pip install -r requirements.txt

# 2. Install Playwright browser and system libraries
playwright install chromium
playwright install-deps