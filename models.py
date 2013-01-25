"""Model base classes.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import datetime
import itertools
import json
import logging
import urlparse

import appengine_config
from webutil import util
from webutil import webapp2

from google.appengine.api import taskqueue
from google.appengine.ext import db


class Source(util.KeyNameModel):
  """A source to read posts from, e.g. a Facebook profile.

  Each concrete source type should subclass this.
  """

  # POLL_TASK_DATETIME_FORMAT = '%Y-%m-%d-%H-%M-%S'
  EPOCH = datetime.datetime.utcfromtimestamp(0)

  # last_polled = db.DateTimeProperty(default=EPOCH)

  # human-readable name for this source type. subclasses should override.
  TYPE_NAME = None

  url = db.LinkProperty()
  picture = db.LinkProperty()

  @classmethod
  def create_new(cls, handler, **kwargs):
    """Creates and saves a new Source and adds a poll task for it.

    Args:
      handler: the current webapp.RequestHandler
      **kwargs: passed to new()
    """
    new = cls.new(handler, **kwargs)
    new.save()
    new.add_scan_task()
    return new

  @classmethod
  def new(cls, handler, **kwargs):
    """Factory method. Creates and returns a new instance for the current user.

    To be implemented by subclasses.

    Args:
      handler: the current webapp.RequestHandler
      **kwargs: passed to new()
    """
    raise NotImplementedError()

  def display_name(self):
    """Returns a human-readable name for this source, e.g. 'My Thoughts'.

    To be implemented by subclasses.
    """
    raise NotImplementedError()

  def type_display_name(self):
    """Returns a human-readable name for this type of source, e.g. 'Facebook'.

    To be implemented by subclasses.
    """
    raise NotImplementedError()

  def get_freedom(self):
    """Returns a list of Freedom template var dicts for posts and their comments.

    To be implemented by subclasses.
    """
    raise NotImplementedError()

  def add_scan_task(self, **kwargs):
    """Adds a scan task for this source."""
    taskqueue.add(queue_name='scan',
                  params={'source_key': str(self.key())},
                  **kwargs)


class Destination(util.KeyNameModel):
  """A web site to propagate posts to, e.g. a WordPress blog.

  Each concrete destination class should subclass this class.
  """

  last_updated = db.DateTimeProperty()

  def add_comment(self, comment):
    """Posts the given comment to this site.

    To be implemented by subclasses.

    Args:
      comment: Comment
    """
    raise NotImplementedError()


class Migration(db.Model):
  """A migration from a single source to a single destination."""
  STATUSES = ('new', 'processing', 'complete')
  source = db.ReferenceProperty(reference_class=Source, required=True)
  dest = db.ReferenceProperty(reference_class=Destination, required=True)


class Migratable(util.KeyNameModel):
  """A post or comment to be migrated.

  Key name is 'POST_ID DEST KEY_NAME', e.g.
  '123_456_789 Wordpress http://snarfed.org/w/_0'. The post id, destination
  kind, and destination key name must not have spaces.

  I could use the two serialized keys instead, but this makes manual inspection
  and debugging easier.
  """
  STATE: here, and migration above

class Post(util.KeyNameModel):
  """A post to be propagated to a single destination.
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

  def propagate(self):
    pass


class Comment(util.KeyNameModel):
  """A comment to be propagated.
  """
  STATUSES = ('new', 'processing', 'complete')

  source = db.ReferenceProperty(reference_class=Source, required=True)
  dest = db.ReferenceProperty(reference_class=Destination, required=True)
  source_post_url = db.LinkProperty()
  source_comment_url = db.LinkProperty()
  dest_post_url = db.LinkProperty()
  dest_comment_url = db.LinkProperty()
  created = db.DateTimeProperty()
  author_name = db.StringProperty()
  author_url = db.LinkProperty()
  content = db.TextProperty()

  status = db.StringProperty(choices=STATUSES, default='new')
  leased_until = db.DateTimeProperty()

  @db.transactional
  def get_or_save(self):
    existing = db.get(self.key())
    if existing:
      logging.debug('Deferring to existing comment %s.', existing.key().name())
      # this might be a nice sanity check, but we'd need to hard code certain
      # properties (e.g. content) so others (e.g. status) aren't checked.
      # for prop in self.properties().values():
      #   new = prop.get_value_for_datastore(self)
      #   existing = prop.get_value_for_datastore(existing)
      #   assert new == existing, '%s: new %s, existing %s' % (prop, new, existing)
      return existing

    logging.debug('New comment to propagate: %s' % self.key().name())
    taskqueue.add(queue_name='propagate', params={'comment_key': str(self.key())})
    self.save()
    return self
