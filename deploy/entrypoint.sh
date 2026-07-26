#!/bin/sh
set -e
cd /app
alembic upgrade head
cd /app/src
exec "$@"
