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
  """Task handler that propagates a single post.

  Request parameters:
    post_key: string key of post entity
  """

  # request deadline (10m) plus some padding
  LEASE_LENGTH = datetime.timedelta(minutes=12)

  def post(self):
    logging.debug('Params: %s', self.request.params)

    try:
      post = self.lease_post()
      if post:
        post.send_slap()
        self.complete_post()
    except Exception, e:
      logging.exception('Propagate task failed')
      if not isinstance(e, exc.HTTPConflict):
        self.release_post()
      raise

  @db.transactional
  def lease_post(self):
    """Attempts to acquire and lease the post entity.

    Returns the Post on success, otherwise None.

    TODO: unify with complete_post
    """
    post = db.get(self.request.params['post_key'])

    if post is None:
      raise exc.HTTPExpectationFailed('no post entity!')
    elif post.status == 'complete':
      # let this response return 200 and finish
      logging.warning('duplicate task already propagated post')
    elif post.status == 'processing' and NOW_FN() < post.leased_until:
      # return error code, but don't raise an exception because we don't want
      # the exception handler in post() to catch it and try to release the lease.
      raise exc.HTTPConflict('duplicate task is currently processing!')
    else:
      assert post.status in ('new', 'processing')
      post.status = 'processing'
      post.leased_until = NOW_FN() + self.LEASE_LENGTH
      post.save()
      return post

  @db.transactional
  def complete_post(self):
    """Attempts to mark the post entity completed.

    Returns True on success, False otherwise.
    """
    post = db.get(self.request.params['post_key'])

    if post is None:
      raise exc.HTTPExpectationFailed('post entity disappeared!')
    elif post.status == 'complete':
      # let this response return 200 and finish
      logging.warning('post stolen and finished. did my lease expire?')
      return
    elif post.status == 'new':
      raise exc.HTTPExpectationFailed('post went backward from processing to new!')

    assert post.status == 'processing'
    post.status = 'complete'
    post.save()

  @db.transactional
  def release_post(self):
    """Attempts to unlease the post entity.
    """
    post = db.get(self.request.params['post_key'])
    if post and post.status == 'processing':
      post.status = 'new'
      post.leased_until = None
      post.save()


application = webapp2.WSGIApplication([
    ('/_ah/queue/scan', Scan),
    ('/_ah/queue/propagate', Propagate),
    ], debug=appengine_config.DEBUG)
