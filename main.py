"""Serves the front page.
"""

__author__ = 'Ryan Barrett <freedom@ryanb.org>'

import logging

import appengine_config
from webutil import handlers
from webutil import webapp2


class FrontPage(handlers.TemplateHandler):
  """Renders and serves /, ie the front page. """

  def template_file(self):
    return 'templates/index.html'

  def force_to_sequence(self):
    return set(['tumblr_hostnames', 'blogger_hostnames'])


application = webapp2.WSGIApplication(
  [('/', FrontPage),
   ],
  debug=appengine_config.DEBUG)
