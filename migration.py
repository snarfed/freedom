"""Serves the migration page.
"""

__author__ = 'Ryan Barrett <freedom@ryanb.org>'

import logging
import urllib

import appengine_config
import models
from webutil import handlers
from webutil import webapp2

import facebook
# import googleplus
# import twitter
import wordpress

from google.appengine.ext import db


class MigrateHandler(webapp2.RequestHandler):
  """Starts a migration."""

  def post(self):
    source = db.Key(self.request.get('source'))
    dest = db.Key(self.request.get('dest'))
    key_name = models.Migration.make_key_name(source.kind(), source.name(),
                                              dest.kind(), dest.name())
    id = db.allocate_ids(db.Key.from_path('Migration', 1), 1)[0]
    models.Migration.get_or_insert(key_name, id=id)
    self.redirect('/migration/%d' % id)


class MigrationHandler(handlers.TemplateHandler):
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
    migration = models.Migration.all().filter('id =', self.id).get()
    logging.info('Got migration %s', migration.key().name())
    return {'migration': migration}


application = webapp2.WSGIApplication(
  [('/migrate', MigrateHandler),
   ('/migration/(.+)', MigrationHandler),
   ],
  debug=appengine_config.DEBUG)
