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
# import googleplus
import migrations
import models
# import twitter
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


# time between propagate requests for posts and comments from a single source
POST_DELAY_SECS = 1


class Scan(webapp2.RequestHandler):
  """Task handler that fetches and processes posts for a single migration.

  Inserts a propagate task for each new post for this migration.

  Request parameters:
    migration: string key name of Migration entity
    scan_url: source API URL to use to scan. usually includes the current paging
      parameters.
  """

  def post(self):
    logging.debug('Params: %s', self.request.params)

    migration = models.Migration.get_by_key_name(self.request.params['migration'])
    if not migration:
      logging.warning('Missing migration! Dropping task.')
      return

    source = migration.source()
    dest = migration.dest()

    scan_url = self.request.get('scan_url')
    logging.info('Scanning %s', scan_url)
    posts, next_scan_url = source.get_posts(migration, scan_url=scan_url)
    for post in posts:
      # this will add a propagate task if the post is new (to us)
      # TODO: delay by X seconds, staggered 1s per post
      post.get_or_save()
      logging.info('Saved post %s', post.key().name())

    # add next scan task
    if next_scan_url:
      new_params = dict(self.request.params)
      new_params['scan_url'] = next_scan_url
      logging.info('Adding next scan task at %s', next_scan_url)
      taskqueue.add(queue_name='scan', params=new_params,
                    countdown=len(posts) * POST_DELAY_SECS)
    else:
      logging.info('No next page, done scanning!')


class Propagate(webapp2.RequestHandler):
  """Task handler that propagates a single post or comment.

  Request parameters:
    key: string key of post or comment
    post: optional. if present, this is a post.
    comment: optional. if present, this is a comment.
  """

  # request deadline (10m) plus some padding
  LEASE_LENGTH = datetime.timedelta(minutes=12)

  def post(self):
    logging.debug('Params: %s', self.request.params)

    try:
      entity = self.lease()
      dest = entity.dest()
      if entity:
        # TODO: port to ndb and use caching
        if 'post' in self.request.params:
          dest.publish_post(entity)
        elif 'comment' in self.request.params:
          dest.publish_comment(entity)
        else:
          logging.error('Skipping bad task without post or comment query param')
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
