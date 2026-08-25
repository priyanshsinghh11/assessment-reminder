# Heroku-shaped platforms (Heroku, Railway, Render, Dokku). $PORT is supplied
# by the platform and gunicorn.conf.py reads it.
#
# ONE DYNO, AND ONE WORKER INSIDE IT. The reminder dedupe now settles its own
# races in Mongo, so that is no longer the reason -- but _run_lock is still a
# threading.Lock, and a second process means two locks, each certain it holds
# the only one, and two concurrent portal scans or grading runs. See the
# workers note in gunicorn.conf.py before scaling anything here.
#
# The ephemeral filesystem is no longer a correctness problem: state/ holds a
# cached portal scan that the next Sync rebuilds, not the dedupe log.
web: gunicorn -c gunicorn.conf.py wsgi:app

# The manager review surface, as its own process. Same image, same command, one
# environment variable apart. Only this one should be publicly routable.
review: REVIEW_ONLY=1 gunicorn -c gunicorn.conf.py wsgi:app
