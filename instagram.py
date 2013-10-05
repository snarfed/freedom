#!/usr/bin/python
"""Instagram source class.

TODO: handle expired access tokens
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import itertools
import json
import logging
import urllib
import urlparse
from webob import exc

from activitystreams import instagram as as_instagram
import appengine_config
import models
from python_instagram.bind import InstagramAPIError
from python_instagram.client import InstagramAPI

from webutil import util

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext.webapp import template
import webapp2


# instagram api url templates. can't (easily) use urllib.urlencode() because i
# want to keep the %(...)s placeholders as is and fill them in later in code.
GET_AUTH_CODE_URL = str('&'.join((
    'https://api.instagram.com/oauth/authorize?',
    'client_id=%(client_id)s',
    # redirect_uri here must be the same in the access token request!
    'redirect_uri=%(host_url)s/instagram/oauth_callback',
    'response_type=code',
    'state=%(state)s',
    )))

GET_ACCESS_TOKEN_URL = 'https://api.instagram.com/oauth/access_token'


class Instagram(models.Source):
  """Implements the Instagram source.

  The key name is the user's Instagram ID.
  """

  DOMAIN = 'instagram.com'

  name = db.StringProperty()  # full human-readable name
  username = db.StringProperty()
  access_token = db.StringProperty()

  def display_name(self):
    return self.name

  @classmethod
  def new(cls, handler, resp):
    """Creates and returns a Instagram instance for the logged in user.

    Args:
      resp: JSON dict response from the Instagram /oauth/access_token request
    """
    assert resp['access_token']
    user = resp['user']
    username = user.get('username')

    return Instagram.get_or_insert(
      user['id'],
      access_token=resp['access_token'],
      name=user.get('full_name'),
      username=username,
      picture=user.get('profile_picture'),
      url=user.get('website', 'http://%s/%s' % (cls.DOMAIN, username)))

  def get_posts(self, migration, scan_url=None):
    """Fetches a page of posts.

    Args:
      migration: Migration
      scan_url: string, the API URL to fetch the current page of posts. If None,
        starts at the beginning.

    Returns:
      (posts, next_url). posts is a sequence of InstagramPosts.
      next_url is a string, the API URL to use for the next scan, or None
      if there is nothing more to scan.
    """
    api = InstagramAPI(client_id=appengine_config.INSTAGRAM_CLIENT_ID,
                       client_secret=appengine_config.INSTAGRAM_CLIENT_SECRET,
                       access_token=self.access_token)

    user_id = self.key().name()
    media, next_url = api.user_recent_media(user_id, with_next_url=scan_url)
    converter = as_instagram.Instagram(None)
    imedia = [InstagramMedia(key_name_parts=(m.id, migration.key().name()),
                             json_data=json.dumps(converter.media_to_activity(m)))
              for m in media]
    return imedia, next_url


class InstagramMedia(models.Migratable):
  """An Instagram photo or video.

  The key name is 'MEDIA_ID MIGRATION_KEY_NAME'.

  The json_data properties in both this class and InstagramComment store
  *ActivityStreams* formatted data, not Instagram' API format. That's because we
  use the python-instagram library, which returns python objects, not JSON.
  """

  TYPE = 'post'

  def to_activity(self):
    """Returns an ActivityStreams activity dict for this media."""
    return self.data()

  def get_comments(self):
    """Returns an iterable of InstagramComments for this media's comments."""
    comments = self.data().get('replies', {}).get('items', [])
    migration_key = InstagramMedia.migration.get_value_for_datastore(self)
    return (InstagramComment(key_name_parts=(cmt['id'], migration_key.name()),
                             json_data=json.dumps(cmt))
            for cmt in comments)


class InstagramComment(models.Migratable):
  """A Instagram comment.

  The key name is 'COMMENT_ID MIGRATION_KEY_NAME'.
  """

  TYPE = 'comment'

  def to_activity(self):
    """Returns an ActivityStreams activity dict for this comment."""
    return {'object': self.data()}


class AddInstagram(webapp2.RequestHandler):
  def post(self):
    """Gets an access token for the current user.

    Actually just gets the auth code and redirects to /instagram/got_auth_code,
    which makes the next request to get the access token.
    """
    dest = self.request.get('dest')
    assert dest

    # http://instagram.com/developer/authentication/
    url = GET_AUTH_CODE_URL % {
      'client_id': appengine_config.INSTAGRAM_CLIENT_ID,
      # TODO: CSRF protection identifier.
      'host_url': self.request.host_url,
      'state': dest,
      }
    self.redirect(str(url))


class GotAuthCode(webapp2.RequestHandler):
  def get(self):
    """Gets an access token based on an auth code."""
    if self.request.get('error'):
      params = [urllib.decode(self.request.get(k))
                for k in ('error', 'error_reason', 'error_description')]
      raise exc.HttpBadRequest('\n'.join(params))

    auth_code = self.request.get('code')
    assert auth_code

    # http://instagram.com/developer/authentication/
    # TODO: handle permission declines, errors, etc
    data = urllib.urlencode({
      'client_id': appengine_config.INSTAGRAM_CLIENT_ID,
      'client_secret': appengine_config.INSTAGRAM_CLIENT_SECRET,
      'code': auth_code,
      'redirect_uri': self.request.host_url + '/instagram/oauth_callback',
      'grant_type': 'authorization_code',
      })

    resp = urlfetch.fetch(GET_ACCESS_TOKEN_URL, method='POST', payload=data,
                          deadline=999)
    try:
      resp = json.loads(resp.content)
    except ValueError, TypeError:
      logging.error('Bad response:\n%s' % resp.content)
      raise

    if 'error_type' in resp:
      error_class = exc.status_map[resp.get('code', 500)]
      raise error_class(resp.get('error_message'))

    # TODO: error handling. handle permission declines, errors, etc
    logging.debug('access token response: %s' % resp)

    inst = Instagram.new(self, resp)

    # redirect so that refreshing the page doesn't try to get a new access token
    # and rewrite the Instagram entity.
    self.redirect('/?%s#options' % urllib.urlencode(
        {'dest': self.request.get('state'),
         'source': urllib.quote(str(inst.key()))}))


application = webapp2.WSGIApplication([
    ('/instagram/source/add', AddInstagram),
    ('/instagram/oauth_callback', GotAuthCode),
    ], debug=appengine_config.DEBUG)
