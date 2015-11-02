"""Tumblr destination.

http://www.tumblr.com/docs/en/api/v2
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import functools
import logging
import os
import re
import urllib
import urllib2
import urlparse

from activitystreams import activitystreams
import appengine_config
import models
import tumblpy
from webob import exc
from webutil import util

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext.webapp import template
import webapp2


# http://www.tumblr.com/oauth/apps
TUMBLR_APP_KEY = appengine_config.read('tumblr_app_key')
TUMBLR_APP_SECRET = appengine_config.read('tumblr_app_secret')

OAUTH_CALLBACK_URL = '%s://%s/tumblr/oauth_callback' % (
  appengine_config.SCHEME, appengine_config.HOST)


class TumblrOAuthRequestToken(models.OAuthToken):
  pass


class TumblrOAuthFinalToken(models.OAuthToken):
  pass


class Tumblr(models.Destination):
  """A Tumblr blog. The key name is the blog hostname."""

  username = db.StringProperty(required=True)
  # title = db.StringProperty(required=True)

  # Tumblr OAuth 1.0A access token for this account
  # http://www.tumblr.com/docs/en/api/v2#auth
  token_key = db.StringProperty(required=True)
  token_secret = db.StringProperty(required=True)

  def hostname(self):
    return self.key().name()

  def display_name(self):
    return self.hostname()

  @classmethod
  def new(cls, handler, **kwargs):
    """Creates and saves a Tumblr entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler
      kwargs: passed through to the Tumblr() constructor

    Returns: Tumblr
    """
    return Tumblr.get_or_insert(handler.request.get('host'),
                                username=handler.request.get('tumblr_username'),
                                **kwargs)

  def publish_post(self, post):
    """Publishes a post.

    Args:
      post: post entity

    Returns: string, the Tumblr post id
    """
    # TODO: expose as option
    # Attach these tags to the Tumblr posts.
    POST_TAGS = ['Freedom']

    activity = post.to_activity()
    obj = activity['object']
    date = util.parse_iso8601(activity['published'])
    location = obj.get('location')
    logging.info('Publishing post %s', obj['id'])

    # extract title
    title = obj.get('title')
    if not title:
      first_phrase = re.search('^[^,.:;?!]+', obj.get('content', ''))
      if first_phrase:
        title = first_phrase.group()
      elif location and 'displayName' in location:
        title = 'At ' + location['displayName']
      else:
        title = date.date().isoformat()

    # date is UTC (ie GMT), formatted e.g. '2012-01-14 12:00:15 GMT'
    if date.utcoffset():
      date = date - date.utcoffset()
    datestr_utc = date.strftime('%Y-%m-%d %H:%M:%S GMT')

    # post params: http://www.tumblr.com/docs/en/api/v2#posting
    body = post.render_html()
    params = {
      'type': 'text',
      # 'tags': POST_TAGS,
      # TODO: ugh, tumblr doesn't let you create a post with a date more than an
      # hour off of the current time. bleh.
      # https://groups.google.com/d/msg/tumblr-api/CYLno2Q60sU/6tR1Xe56TiIJ
      # 'date': datestr_utc,
      'format': 'html',
      # 'title': title,
      'body': body,
      }

    # photo
    image_url = obj.get('image', {}).get('url')
    if obj.get('objectType') == 'photo' and image_url:
      params.update({'type': 'photo',
                     'source': image_url,
                     'caption': body,
                     })
      del params['body']
      # del params['title']

    # post!
    tp = tumblpy.Tumblpy(app_key=TUMBLR_APP_KEY,
                         app_secret=TUMBLR_APP_SECRET,
                         oauth_token=self.token_key,
                         oauth_token_secret=self.token_secret)

    logging.info('Creating post with params: %r', params)
    resp = tp.post('post', blog_url=self.hostname(), params=params)
    return str(resp['id'])

  def publish_comment(self, comment):
    """Tumblr doesn't support comments, so this is a noop.

    I could maybe put comments in submitted posts:
    http://www.tumblr.com/docs/en/using_messages#submit

    ...but it doesn't look like they're exposed in the API.

    Args:
      comment: comment entity
    """
    logging.info("Tumblr doesn't support comments; skipping: %r", comment.data())


# TODO: unify with other dests, sources?
class ConnectTumblr(webapp2.RequestHandler):
  def post(self):
    tp = tumblpy.Tumblpy(app_key=TUMBLR_APP_KEY,
                         app_secret=TUMBLR_APP_SECRET)
    auth_props = tp.get_authentication_tokens(callback_url=OAUTH_CALLBACK_URL)

    # store the request token for later use in the callback handler
    TumblrOAuthRequestToken.new(auth_props['oauth_token'],
                                auth_props['oauth_token_secret'])
    auth_url = auth_props['auth_url']
    logging.info('Generated request token, redirecting to Tumblr: %s', auth_url)
    self.redirect(auth_url)


class OAuthCallback(webapp2.RequestHandler):
  """OAuth callback. Fetches the user's blogs and re-renders the front page."""
  def get(self):
    # lookup the request token
    token_key = self.request.get('oauth_token')
    token = TumblrOAuthRequestToken.get_by_key_name(token_key)
    if token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % token_key)

    # generate and store the final token
    tp = tumblpy.Tumblpy(app_key=TUMBLR_APP_KEY,
                         app_secret=TUMBLR_APP_SECRET,
                         oauth_token=token_key,
                         oauth_token_secret=token.secret)
    auth_token = tp.get_authorized_tokens(self.request.params['oauth_verifier'])
    final_token = auth_token['oauth_token']
    final_secret = auth_token['oauth_token_secret']
    TumblrOAuthFinalToken.new(final_token, final_secret)

    # get the user's blogs
    # http://www.tumblr.com/docs/en/api/v2#user-methods
    tp = tumblpy.Tumblpy(app_key=TUMBLR_APP_KEY,
                         app_secret=TUMBLR_APP_SECRET,
                         oauth_token=final_token,
                         oauth_token_secret=final_secret)
    resp = tp.post('user/info')
    logging.debug(resp)
    user = resp['user']
    hostnames = [util.domain_from_link(b['url']) for b in user['blogs']]
    hostnames = util.trim_nulls(hostnames)
    # titles = [b[title] for b in user['blogs']]

    # redirect so that refreshing the page doesn't try to regenerate the oauth
    # token, which won't work.
    self.redirect('/?' + urllib.urlencode({
          'tumblr_username': user['name'],
          'tumblr_hostnames': hostnames,
           # 'tumblr_titles': titles,
          'oauth_token': auth_token['oauth_token'],
          }, True))


class AddTumblr(webapp2.RequestHandler):
  def post(self):
    # lookup final OAuth token
    token_key = self.request.get('oauth_token')
    token = TumblrOAuthFinalToken.get_by_key_name(token_key)
    if token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % oauth_token)

    tumblr = Tumblr.new(self, token_key=token_key, token_secret=token.secret)

    # redirect so that refreshing the page doesn't try to regenerate the oauth
    # token, which won't work.
    self.redirect('/?dest=%s#sources' % str(tumblr.key()))


class DeleteTumblr(webapp2.RequestHandler):
  def post(self):
    site = Tumblr.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


application = webapp2.WSGIApplication([
    ('/tumblr/dest/connect', ConnectTumblr),
    ('/tumblr/oauth_callback', OAuthCallback),
    ('/tumblr/dest/add', AddTumblr),
    ('/tumblr/dest/delete', DeleteTumblr),
    ], debug=appengine_config.DEBUG)
