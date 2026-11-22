#!/bin/sh
# Download the offline outage library into DIR and build DIR/START_HERE.html.
# Usage: ./download.sh DIR [--with-wikipedia]
exec python3 -I "$(dirname "$0")/outage_guide.py" download "$@"
