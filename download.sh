#!/bin/sh
# Download the offline outage library and build START_HERE.html.
# Usage: ./download.sh [DIR] [--with-wikipedia]    (DIR defaults to this folder)
exec python3 -I "$(dirname "$0")/outage_guide.py" download "$@"
