#!/usr/bin/python
"""Source base class, shared by all social network classes.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import datetime
import itertools
import logging
import urlparse

import appengine_config
from webutil import util
from webutil import webapp2

from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext.webapp.util import run_wsgi_app


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
