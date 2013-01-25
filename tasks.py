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
import googleplus
import twitter
from webutil import webapp2

from google.appengine.ext import db
from google.appengine.api import taskqueue

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
# - injecting a function dependency, ie Poll(now=datetime.datetime.now), worked
# in webapp 1, which I used in bridgy, like this:
#
#   application = webapp.WSGIApplication([
#     ('/_ah/queue/poll', lambda: Poll(now=lambda: self.now)),
#     ...
#
# However, it fails with this error in webapp2:
#
#   File ".../webapp2.py", line 1511, in __call__
#     return response(environ, start_response)
#   TypeError: 'Poll' object is not callable

NOW_FN = datetime.datetime.now


class Scan(webapp2.RequestHandler):
  """Task handler that fetches and processes posts from a single source.

  Inserts a propagate task for each post that hasn't been seen before.

  Request parameters:
    source_key: string key of source entity
  """

  def post(self):
    logging.debug('Params: %s', self.request.params)

    key = self.request.params['source_key']
    source = db.get(key)
    if not source:
      logging.warning('Source not found! Dropping task.')
      return

    logging.debug('Scanning %s source %s', source.kind(), source.key().name())
    for post in source.get_more_posts():
      # this will add a propagate task if the post is new to us
      post.Post(key_name=vars['id'], vars=json.dumps(vars)).get_or_save()

    source.add_scan_task(countdown=self.TASK_COUNTDOWN.seconds)
    source.save()


class Propagate(webapp2.RequestHandler):
  """Task handler that propagates a single post or comment.

  Request parameters:
    key: string key of post or comment
  """

  # request deadline (10m) plus some padding
  LEASE_LENGTH = datetime.timedelta(minutes=12)

  def post(self):
    logging.debug('Params: %s', self.request.params)

    try:
      entity = self.lease()
      if entity:
        entity.propagate()
        self.complete()
    except Exception, e:
      logging.exception('Propagate task failed')
      if not isinstance(e, exc.HTTPConflict):
        self.release()
      raise

  @db.transactional
  def lease(self):
    """Attempts to acquire and lease the post or comment entity.

    Returns the entity on success, otherwise None.
    """
    entity = db.get(self.request.params['key'])

    if entity is None:
      raise exc.HTTPExpectationFailed('entity not found!')
    elif entity.status == 'complete':
      # let this response return 200 and finish
      logging.warning('duplicate task already propagated post/comment')
    elif entity.status == 'processing' and NOW_FN() < entity.leased_until:
      # return error code, but don't raise an exception because we don't want
      # the exception handler in post() to catch it and try to release the lease.
      raise exc.HTTPConflict('duplicate task is currently processing!')
    else:
      assert entity.status in ('new', 'processing')
      entity.status = 'processing'
      entity.leased_until = NOW_FN() + self.LEASE_LENGTH
      entity.save()
      return entity

  @db.transactional
  def complete(self):
    """Attempts to mark the post or comment entity completed.
    """
    entity = db.get(self.request.params['key'])

    if entity is None:
      raise exc.HTTPExpectationFailed('entity disappeared!')
    elif entity.status == 'complete':
      # let this response return 200 and finish
      logging.warning('post/comment stolen and finished. did my lease expire?')
      return
    elif entity.status == 'new':
      raise exc.HTTPExpectationFailed(
        'post/comment went backward from processing to new!')

    assert entity.status == 'processing'
    entity.status = 'complete'
    entity.save()

  @db.transactional
  def release(self):
    """Attempts to release the lease on the post or comment entity.
    """
    entity = db.get(self.request.params['key'])
    if entity and entity.status == 'processing':
      entity.status = 'new'
      entity.leased_until = None
      entity.save()


application = webapp2.WSGIApplication([
    ('/_ah/queue/scan', Scan),
    ('/_ah/queue/propagate', Propagate),
    ], debug=appengine_config.DEBUG)
