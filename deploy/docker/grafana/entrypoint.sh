#!/bin/sh
# Inject custom CSS link into Grafana's index.html before starting
INDEX=/usr/share/grafana/public/views/index.html
if [ -f "$INDEX" ] && ! grep -q 'custom.css' "$INDEX"; then
  sed -i 's|</head>|<link rel="stylesheet" href="/public/build/custom.css" /></head>|' "$INDEX"
fi
exec /run.sh "$@"
