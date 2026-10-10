#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear -v 0

if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null \
        && echo "Created superuser $DJANGO_SUPERUSER_USERNAME" \
        || echo "Superuser $DJANGO_SUPERUSER_USERNAME already exists"
fi

if [ "$SEED_DEMO_DATA" = "1" ]; then
    python manage.py seed_demo
fi

exec "$@"
