#!/usr/bin/python
"""Post model base class."""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import datetime
import logging
import json
import appengine_config
from webutil import util

from google.appengine.api import taskqueue
from google.appengine.ext import db


class Post(util.KeyNameModel):
  """A post to be propagated to a single destination.

  Key name is 'POST_ID DEST KEY_NAME', e.g.
  '123_456_789 Wordpress http://snarfed.org/w/_0'. The post id, destination
  kind, and destination key name must not have spaces.

  I could use the two serialized keys instead, but this makes manual inspection
  and debugging easier.
  """

  STATUSES = ('new', 'processing', 'complete')

  status = db.StringProperty(choices=STATUSES, default='new')
  leased_until = db.DateTimeProperty()
  # JSON data for this post from the source social network's API.
  data = db.StringProperty()

  @db.transactional
  def get_or_save(self):
    existing = db.get(self.key())
    if existing:
      logging.debug('Deferring to existing post %s.', existing.key().name())
      return existing

    logging.debug('New post to propagate: %s' % self.key().name())
    taskqueue.add(queue_name='propagate', params={'post_key': str(self.key())})
    self.save()
    return self

  @staticmethod
  def make_key_name(id, dest):
    """Returns the key name to use for a given post id and destination.

    Args:
      id: string, post id
      dest: Destination instance
    """
    parts = (id, dest.kind(), dest.key_name())
    for part in parts:
      assert ' ' not in part
    key_name = '%s %s %s' % parts

  def send_slap(self):
    util.urlfetch(post.endpoint, method='POST', payload=self.envelope(),
                  headers={'Content-Type': 'application/magic-envelope+xml'})
