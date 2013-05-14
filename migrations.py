"""Serves the migration page.
"""

__author__ = 'Ryan Barrett <freedom@ryanb.org>'

import itertools
import logging
import urllib
from webob import exc

import appengine_config
import models
from webutil import handlers
from webutil import webapp2

import blogger
import facebook
import googleplus
import tumblr
import twitter
import wordpress_xmlrpc

from google.appengine.api import taskqueue
from google.appengine.ext import db


class MigrateHandler(webapp2.RequestHandler):
  """Starts a migration."""
  # TODO
  # @db.transactional
  def post(self):
    source = db.Key(self.request.get('source'))
    dest = db.Key(self.request.get('dest'))
    key_name = models.Migration.make_key_name(source.kind(), source.name(),
                                              dest.kind(), dest.name())
    id = db.allocate_ids(db.Key.from_path('Migration', 1), 1)[0]
    migration = models.Migration.get_or_insert(key_name, id=id)

    taskqueue.add(queue_name='scan', params={'migration': key_name})
    self.redirect('/migration/%d' % migration.id)


class StopHandler(webapp2.RequestHandler):
  """Stops a migration if it's currently running."""

  message = 'Stopped migration.'

  def post(self, id):
    @db.transactional
    def stop(key):
      migration = models.Migration.get(key)
      if not migration.stopped:
        migration.stopped = True
        migration.save()
      else:
        self.message = 'Migration is already stopped.'

    id = int(id)
    stop(models.Migration.all().filter('id =', id).get().key())
    self.redirect('/migration/%d' % id)


class ResumeHandler(webapp2.RequestHandler):
  """Resumes a migration if it's currently stopped."""

  message = 'Resumed migration.'

  def post(self, id):
    @db.transactional
    def resume(key):
      migration = models.Migration.get(key)
      if migration.stopped:
        migration.stopped = False
        migration.save()
      else:
        self.message = 'Migration is already running.'

    id = int(id)
    resume(models.Migration.all().filter('id =', id).get().key())
    self.redirect('/migration/%d' % id)


class MigrationHandler(handlers.TemplateHandler):
  """Shows the status page for a migration."""

  # map source kind to model classes for that source
  MIGRATABLES = {
    'Facebook': (facebook.FacebookPost, facebook.FacebookComment),
    'GooglePlus': (googleplus.GooglePlusPost, googleplus.GooglePlusComment),
    'Twitter': (twitter.Tweet, twitter.Reply),
    }

  """Renders and serves the migration page.

  Attributes:
    id: integer, migration id
  """
  def get(self, id):
    self.id = int(id)
    super(MigrationHandler, self).get()

  def template_file(self):
    return 'templates/migration.html'

  def template_vars(self):
    logging.info('Fetching migration id %d.', self.id)

    # TODO: port to ndb so these queries can run in parallel
    migration = models.Migration.all().filter('id =', self.id).get()
    if not migration:
      raise exc.HTTPBadRequest('Migration id %d not found.' % self.id)
    logging.info('Got migration %s', migration.key().name())

    logging.info('Fetching posts and comments')
    source_kind = migration.source_key().kind()
    migratables = {
      status: itertools.chain(*(
        cls.all().filter('migration =', migration.key())
          .filter('status =', status)
          .order('-last_updated')
          .fetch(20)
        for cls in self.MIGRATABLES[source_kind]))
      for status in models.Migratable.STATUSES}

    return {'migration': migration,
            'migratables': migratables,
            'message': self.request.get('message')}


application = webapp2.WSGIApplication(
  [('/migrate', MigrateHandler),
   ('/migration/([^/]+)', MigrationHandler),
   ('/migration/([^/]+)/stop', StopHandler),
   ('/migration/([^/]+)/resume', ResumeHandler),
   ],
  debug=appengine_config.DEBUG)
