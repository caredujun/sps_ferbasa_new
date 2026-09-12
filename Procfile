release: python manage.py makemigrations
release: python manage.py migrate
web: gunicorn sps.wsgi --log-file -
#web: gunicorn sps.wsgi --timeout 0
worker: celery -A sps worker -l INFO
#beat: celery -A sps beat --scheduler django_celery_beat.schedulers:DatabaseScheduler -l info  # Só deve ser liberada se for usar o celery beats


