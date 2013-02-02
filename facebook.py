#!/usr/bin/python
"""Facebook source class.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import itertools
import json
import logging
import urllib
import urlparse

import appengine_config
import models

from webutil import util
from webutil import webapp2

from google.appengine.api import urlfetch
from google.appengine.ext import db

# facebook api url templates. can't (easily) use urllib.urlencode() because i
# want to keep the %(...)s placeholders as is and fill them in later in code.
# TODO: use appengine_config.py for local mockfacebook vs prod facebook
GET_AUTH_CODE_URL = str('&'.join((
    'https://www.facebook.com/dialog/oauth/?'
    'scope=read_stream,offline_access',
    'client_id=%(client_id)s',
    # redirect_uri here must be the same in the access token request!
    'redirect_uri=%(host_url)s/facebook/got_auth_code',
    'response_type=code',
    'state=%(state)s',
    )))

GET_ACCESS_TOKEN_URL = str('&'.join((
    'https://graph.facebook.com/oauth/access_token?'
    'client_id=%(client_id)s',
    # redirect_uri here must be the same in the oauth request!
    # (the value here doesn't actually matter since it's requested server side.)
    'redirect_uri=%(host_url)s/facebook/got_auth_code',
    'client_secret=%(client_secret)s',
    'code=%(auth_code)s',
    )))

API_USER_URL = 'https://graph.facebook.com/%(id)s?access_token=%(access_token)s'
API_POSTS_URL = 'https://graph.facebook.com/%(id)s/posts?access_token=%(access_token)s'


class Facebook(models.Source):
  """Implements the Facebook source.

  The key name is the user's or page's Facebook ID.
  """

  DOMAIN = 'facebook.com'

  # full human-readable name
  name = db.StringProperty()

  # the token should be generated with the offline_access scope so that it
  # doesn't expire. details: http://developers.facebook.com/docs/authentication/
  access_token = db.StringProperty()

  def display_name(self):
    return self.name

  def type_display_name(self):
    return 'Facebook'

  @staticmethod
  def new(handler, access_token=None):
    """Creates and returns a Facebook instance for the logged in user.

    Args:
      handler: the current webapp2.RequestHandler
    """
    assert access_token
    resp = util.urlfetch(API_USER_URL % {'id': 'me', 'access_token': access_token})
    me = json.loads(resp)

    id = me['id']
    return Facebook.get_or_insert(
      id,
      access_token=access_token,
      name=me.get('name'),
      picture='https://graph.facebook.com/%s/picture?type=small' % id,
      url='http://facebook.com/%s' % id)

  def get_posts(self, migration, scan_url=None):
    """Fetches a page of posts.

    Args:
      scan_url: string, the API URL to fetch the current page of posts. If None,
        starts at the beginning.

    Returns:
      (posts, next_scan_url). posts is a sequence of FacebookPosts.
      next_scan_url is a string, the API URL to use for the next scan, or None
      if there is nothing more to scan.
    """
    if not scan_url:
      scan_url = API_POSTS_URL % {'id': self.key().name(),
                                  'access_token': self.access_token}
    resp = json.loads(util.urlfetch(scan_url))

    posts = [FacebookPost(key_name_parts=(post['id'], migration.key().name()),
                          data=json.dumps(post))
             for post in resp['data']]
    next_scan_url = resp.get('paging', {}).get('next')
    return posts, next_scan_url


class FacebookPost(models.Migratable):
  """A Facebook post.

  The key name is 'POST_ID MIGRATION_KEY_NAME'.
  """

  def propagate(self, dest):
    """Propagates this post or comment to its destination.
    """
    logging.info('Propagating %s', self.key().name())


class AddFacebook(webapp2.RequestHandler):
  def post(self):
    """Gets an access token for the current user.

    Actually just gets the auth code and redirects to /facebook/got_auth_code,
    which makes the next request to get the access token.
    """
    # redirect_uri = '/'
    dest = self.request.get('dest')
    assert dest

    url = GET_AUTH_CODE_URL % {
      'client_id': appengine_config.FACEBOOK_APP_ID,
      # TODO: CSRF protection identifier.
      # http://developers.facebook.com/docs/authentication/
      'host_url': self.request.host_url,
      # 'state': self.request.host_url + redirect_uri,
      # 'state': urllib.quote(json.dumps({'redirect_uri': redirect_uri})),
      'state': dest,
      }
    self.redirect(str(url))


class GotAuthCode(webapp2.RequestHandler):
  def get(self):
    """Gets an access token based on an auth code."""
    auth_code = self.request.get('code')
    assert auth_code

    # TODO: handle permission declines, errors, etc
    url = GET_ACCESS_TOKEN_URL % {
      'auth_code': auth_code,
      'client_id': appengine_config.FACEBOOK_APP_ID,
      'client_secret': appengine_config.FACEBOOK_APP_SECRET,
      'host_url': self.request.host_url,
      }
    resp = urlfetch.fetch(url, deadline=999)
    # TODO: error handling. handle permission declines, errors, etc
    logging.debug('access token response: %s' % resp.content)
    params = urlparse.parse_qs(resp.content)

    fb = Facebook.new(self, access_token=params['access_token'][0])
    # self.redirect(urllib.unquote(self.request.get('state')))
    self.redirect('/?dest=%s&source=%s' % (self.request.get('state'),
                                           urllib.quote(str(fb.key()))))


application = webapp2.WSGIApplication([
    ('/facebook/source/add', AddFacebook),
    ('/facebook/got_auth_code', GotAuthCode),
    ], debug=appengine_config.DEBUG)
