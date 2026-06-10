#!/bin/sh
# Run xinput test on virtual core pointer (id=2) for 10 seconds
# This shows all events the master pointer generates, including ButtonPress from touchscreen
export DISPLAY=:0
export XAUTHORITY=/root/.Xauthority
timeout 10 xinput test 2 2>&1
echo "Done"
