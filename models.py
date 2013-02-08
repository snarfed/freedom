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


class Base(util.KeyNameModel):
  """A model class with a few utilities.

  - Supports key name of multiple strings separated by spaces. The component
    strings themselves must not have spaces in them.
  - Includes type_display_name() and display_name() methods.
  """

  def __init__(self, *args, **kwargs):
    """Constructs a key name if the key_name_parts kwarg is privded.

    If key_name_parts is in kwargs, key_name must not also be.
    """
    parts = kwargs.get('key_name_parts')
    if parts:
      assert 'key_name' not in kwargs
      kwargs['key_name'] = self.make_key_name(*parts)
    super(Base, self).__init__(*args, **kwargs)

  @staticmethod
  def make_key_name(*args):
    """Makes and returns a key name from the given component strings."""
    return ' '.join(args)

  def key_name_parts(self):
    """Returns the key name component strings as a list."""
    return self.key().name().split(' ')

  def display_name(self):
    """Returns a human-readable name for this source, e.g. 'My Thoughts'.

    To be implemented by subclasses.
    """
    raise NotImplementedError()

  def type_display_name(self):
    """Returns a human-readable name for this type of source, e.g. 'Facebook'.

    Defaults to the model class's kind. May be overridden by subclasses.
    """
    return self.key().kind()


class Source(Base):
  """A source to read posts from, e.g. a Facebook profile.

  Each concrete source type should subclass this.
  """

  url = db.LinkProperty()
  picture = db.LinkProperty()

  @classmethod
  def new(cls, handler, **kwargs):
    """Factory method. Creates and returns a new instance for the current user.

    To be implemented by subclasses.

    Args:
      handler: the current webapp.RequestHandler
      **kwargs: passed to new()
    """
    raise NotImplementedError()

  def get_posts(self, migration, scan_url):
    """Fetches a page of Post instances using the given source API URL.

    To be implemented by subclasses.

    Args:
      migration: Migration
      scan_url: string, the source API URL to fetch the current page of posts

    Returns:
      (posts, next_scan_url). post is a sequence of Migratable instances,
      next_scan_url is a string, the source API URL to use for the next scan, or
      None if there are no more posts.
    """
    raise NotImplementedError()


class Destination(Base):
  """A web site to propagate posts to, e.g. a WordPress blog.

  Each concrete destination class should subclass this class.
  """

  def publish_post(self, post):
    """Publishes a post, idempotently, and returns its (destination) id.

    To be implemented by subclasses.

    In order to be idempotent, this should include the post id in the
    destination post so that it can be queried later to see if it's
    already been published or not.
    """
    raise NotImplementedError()

  def publish_comment(self, comment):
    """Publishes a comment, idempotently, and returns its (destination) id.

    To be implemented by subclasses.

    In order to be idempotent, this should include the comment id in the
    destination comment so that it can be queried later to see if it's
    already been published or not.
    """
    raise NotImplementedError()


class Migration(Base):
  """A migration from a single source to a single destination.

  Key name is 'SOURCE_KIND SOURCE_KEY_NAME DEST_KIND DEST_KEY_NAME', e.g.
  'Facebook 123 Wordpress http://snarfed.org/w/_0'. The four components must not
  have spaces in them.
  """

  STATUSES = ('new', 'processing', 'complete')
  status = db.StringProperty(choices=STATUSES, default='new')
  id = db.IntegerProperty(required=True)

  # lazily cached entities
  cached_source = None
  cached_dest = None

  def source_key(self):
    """Returns the Key for this migration's source."""
    return db.Key.from_path(*self.key_name_parts()[:2])

  def source(self):
    """Returns this migration's source. Caches lazily."""
    if self.cached_source is None:
      self.cached_source = db.get(self.source_key())
    return self.cached_source

  def dest_key(self):
    """Returns the Key for this migration's destination."""
    return db.Key.from_path(*self.key_name_parts()[2:])

  def dest(self):
    """Returns this migration's destination. Caches lazily."""
    if self.cached_dest is None:
      self.cached_dest = db.get(self.dest_key())
    return self.cached_dest


class Migratable(Base):
  """A post or comment to be migrated.

  The key name is 'ID MIGRATION_KEY_NAME', where ID is the source-specific id of
  the post or comment and must not have spaces in it.
  """

  TYPE = None  # subclasses should set this to 'post' or 'comment'
  STATUSES = ('new', 'processing', 'complete')

  status = db.StringProperty(choices=STATUSES, default='new')
  last_updated = db.DateTimeProperty(auto_now=True)
  leased_until = db.DateTimeProperty()
  # JSON data for this post from the source social network's API.
  json_data = db.TextProperty()
  # duplicated here (as well as in the key name) so it can be queried.
  migration = db.ReferenceProperty(Migration)
  # the destination-specific id of the migrated copy of this entity
  dest_id = db.StringProperty()
  # only populated for comments
  dest_post_id = db.StringProperty()

  # dict, cached copy of decoded JSON data
  parsed_data = None

  def to_activity(self):
    """Returns an ActivityStreams activity dict for this post or comment.

    To be implemented by subclasses.
    """
    raise NotImplementedError()

  def get_comments(self):
    """Returns a list of entities for this post's comments."""
    return []

  @db.transactional
  def get_or_save(self, task_countdown=0):
    """Like get_or_insert, and adds a propagate task."""
    entity = db.get(self.key())
    key_str = '%s %s' % (self.kind(), self.key().name())
    if entity:
      logging.info('Deferring to existing entity: %s', key_str)
    else:
      logging.info('Creating new entity: %s', key_str)
      self.migration = db.Key.from_path('Migration',
                                        ' '.join(self.key_name_parts()[1:]))
      self.save()
      entity = self

    if entity.status == 'new':
      logging.info('Adding propagate task')
      taskqueue.add(queue_name='propagate',
                    params={'kind': self.kind(), 'key_name': self.key().name()},
                    countdown=task_countdown,
                    transactional=True)
    return entity

  def id(self):
    """Returns the source id of this post or comment."""
    return self.key_name_parts()[0]

  def dest(self):
    """Returns the destination for this post or comment."""
    return db.get(db.Key.from_path(*self.key_name_parts()[3:]))

  def data(self):
    """Returns the JSON data as a dict. Parses lazily and caches the result."""
    if self.parsed_data is None:
      self.parsed_data = json.loads(self.json_data)
    return self.parsed_data
