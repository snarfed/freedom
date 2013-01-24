"""Task queue handlers.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import datetime
import itertools
import json
import logging
import re
import time
from webob import exc

# need to import model class definitions since scan creates and saves entities.
import facebook
from models import Source
import googleplus
import salmon
import twitter
from webutil import webapp2

from google.appengine.ext import db
from google.appengine.api import taskqueue
from google.appengine.ext.webapp.util import run_wsgi_app

import appengine_config


# Unit tests use NOW_FN (below) to inject a fake for datetime.datetime.now. Lots
# of other techniques for this failed:
#
# - mox can only expect a mocked call exactly N times or at least once, zero or
# more times, which is what this needs.
#
# - datetime.datetime.now is a "built-in/extension" type so I can't set
# it manually via monkey patch.
#
# - injecting a function dependency, ie Scan(now=datetime.datetime.now), worked
# in webapp 1, which I used in bridgy, like this:
#
#   application = webapp.WSGIApplication([
#     ('/_ah/queue/scan', lambda: Scan(now=lambda: self.now)),
#     ...
#
# However, it fails with this error in webapp2:
#
#   File ".../webapp2.py", line 1511, in __call__
#     return response(environ, start_response)
#   TypeError: 'Scan' object is not callable

NOW_FN = datetime.datetime.now


class Scan(webapp2.RequestHandler):
  """Task handler that fetches and processes posts from a single source.

  Inserts a propagate task for each post that hasn't been seen before.

  Request parameters:
    source_key: string key of source entity
    last_scanned: timestamp, YYYY-MM-DD-HH-MM-SS
  """

  TASK_COUNTDOWN = datetime.timedelta(hours=1)

  def post(self):
    logging.debug('Params: %s', self.request.params)

    key = self.request.params['source_key']
    source = db.get(key)
    if not source:
      logging.warning('Source not found! Dropping task.')
      return

    last_scanned = self.request.params['last_scanned']
    if last_scanned != source.last_scanned.strftime(Source.SCAN_TASK_DATETIME_FORMAT):
      logging.warning('duplicate scan task! deferring to the other task.')
      return

    logging.debug('Scaning %s source %s', source.kind(), source.key().name())
    for vars in source.get_salmon():
      logging.debug('Got salmon %r', vars)
      salmon.Salmon(key_name=vars['id'], vars=json.dumps(vars)).get_or_save()

    source.last_scanned = NOW_FN()
    source.add_scan_task(countdown=self.TASK_COUNTDOWN.seconds)
    source.save()


class Propagate(webapp2.RequestHandler):
  """Task handler that propagates a single salmon.

  Request parameters:
    salmon_key: string key of salmon entity
  """

  # request deadline (10m) plus some padding
  LEASE_LENGTH = datetime.timedelta(minutes=12)

  def post(self):
    logging.debug('Params: %s', self.request.params)

    try:
      salmon = self.lease_salmon()
      if salmon:
        salmon.send_slap()
        self.complete_salmon()
    except Exception, e:
      logging.exception('Propagate task failed')
      if not isinstance(e, exc.HTTPConflict):
        self.release_salmon()
      raise

  @db.transactional
  def lease_salmon(self):
    """Attempts to acquire and lease the salmon entity.

    Returns the Salmon on success, otherwise None.

    TODO: unify with complete_salmon
    """
    salmon = db.get(self.request.params['salmon_key'])

    if salmon is None:
      raise exc.HTTPExpectationFailed('no salmon entity!')
    elif salmon.status == 'complete':
      # let this response return 200 and finish
      logging.warning('duplicate task already propagated salmon')
    elif salmon.status == 'processing' and NOW_FN() < salmon.leased_until:
      # return error code, but don't raise an exception because we don't want
      # the exception handler in post() to catch it and try to release the lease.
      raise exc.HTTPConflict('duplicate task is currently processing!')
    else:
      assert salmon.status in ('new', 'processing')
      salmon.status = 'processing'
      salmon.leased_until = NOW_FN() + self.LEASE_LENGTH
      salmon.save()
      return salmon

  @db.transactional
  def complete_salmon(self):
    """Attempts to mark the salmon entity completed.

    Returns True on success, False otherwise.
    """
    salmon = db.get(self.request.params['salmon_key'])

    if salmon is None:
      raise exc.HTTPExpectationFailed('salmon entity disappeared!')
    elif salmon.status == 'complete':
      # let this response return 200 and finish
      logging.warning('salmon stolen and finished. did my lease expire?')
      return
    elif salmon.status == 'new':
      raise exc.HTTPExpectationFailed('salmon went backward from processing to new!')

    assert salmon.status == 'processing'
    salmon.status = 'complete'
    salmon.save()

  @db.transactional
  def release_salmon(self):
    """Attempts to unlease the salmon entity.
    """
    salmon = db.get(self.request.params['salmon_key'])
    if salmon and salmon.status == 'processing':
      salmon.status = 'new'
      salmon.leased_until = None
      salmon.save()


application = webapp2.WSGIApplication([
    ('/_ah/queue/scan', Scan),
    ('/_ah/queue/propagate', Propagate),
    ], debug=appengine_config.DEBUG)
