"""Serves the front page.
"""

__author__ = 'Ryan Barrett <freedom@ryanb.org>'

import appengine_config
from webutil import handlers
from webutil import webapp2


class FrontPage(handlers.TemplateHandler):
  """Renders and serves /, ie the front page. """

  def template_file(self):
    return 'templates/index.html'

  def template_vars(self):
    return self.request.params


application = webapp2.WSGIApplication(
  [('/', FrontPage),
   ],
  debug=appengine_config.DEBUG)
