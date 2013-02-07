#!/usr/bin/python
"""Google+ source class.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import json
import logging
import urllib
import urlparse

from activitystreams import googleplus as as_googleplus
import appengine_config
import models

from webutil import util
from webutil import webapp2

from apiclient import discovery
from apiclient.errors import HttpError
from apiclient.model import BaseModel
from oauth2client.appengine import CredentialsModel
from oauth2client.appengine import OAuth2Decorator
from oauth2client.appengine import StorageByKeyName
from google.appengine.api import memcache
from google.appengine.api import urlfetch
from google.appengine.ext import db

OAUTH_CALLBACK = '%s://%s/googleplus/oauth2callback?dest=%%s' % (appengine_config.SCHEME,
                                                                 appengine_config.HOST)


json_service = discovery.build('plus', 'v1')
oauth = OAuth2Decorator(
  client_id=appengine_config.GOOGLEPLUS_CLIENT_ID,
  client_secret=appengine_config.GOOGLEPLUS_CLIENT_SECRET,
  # G+ scopes: https://developers.google.com/+/api/oauth#oauth-scopes
  scope='https://www.googleapis.com/auth/plus.me')


class GooglePlus(models.Source):
  """A Google+ account. The key name is the Google+ user id."""

  DOMAIN = 'googleplus.com'

  # Google+ OAuth 1.0A access token for this account
  # https://dev.googleplus.com/docs/auth/3-legged-authorization
  token_key = db.StringProperty()
  token_secret = db.StringProperty()

  def display_name(self):
    return self.key().name()

  @staticmethod
  def new(handler, token_key=None, token_secret=None):
    """Creates and returns a GooglePlus instance for the authenticated user.

    Args:
      handler: the current webapp2.RequestHandler
    """
    tw = as_googleplus.GooglePlus(handler)
    me = tw.get_actor(access_token_key=token_key,
                      access_token_secret=token_secret)
    return GooglePlus.get_or_insert(
      me['username'],
      token_key=token_key,
      token_secret=token_secret,
      picture=me['image']['url'],
      url=me['url'])

  def get_posts(self, migration, scan_url=None):
    """Fetches a page of posts.

    Args:
      scan_url: string, the API URL to fetch the current page of posts. If None,
        starts at the beginning.

    Returns:
      (posts, next_scan_url). posts is a sequence of Posts.
      next_scan_url is a string, the API URL to use for the next scan, or None
      if there is nothing more to scan.
    """
    # TODO: expose as options
    # https://dev.googleplus.com/docs/api/1.1/get/statuses/user_timeline
    INCLUDE_REPOSTS = False    # ?exclude_replies=false
    INCLUDE_AT_REPLIES = False  # ?include_rts=true

    # Don't publish posts from these applications
    APPLICATION_BLACKLIST = ('Likes', 'Links', 'googleplusfeed')

    if not scan_url:
      scan_url = API_POSTS_URL % self.key().name()
    tw = as_googleplus.GooglePlus(None)
    resp = json.loads(tw.urlfetch(scan_url,
                                  access_token_key=self.token_key,
                                  access_token_secret=self.token_secret))

    posts = []
    for post in resp:
      id = post['id']
      app = post.get('source')
      if app and app in APPLICATION_BLACKLIST:
        logging.info('Skipping post %d', id)
        continue

      posts.append(Post(key_name_parts=(str(id), migration.key().name()),
                          json_data=json.dumps(post)))

    next_scan_url = None
    if posts:
      scan_url + '&max_id=%s' % posts[-1].id()
    # XXX remove
    if posts and tw.rfc2822_to_iso8601(posts[-1].data()['created_at']) < '2013--01-01':
      next_scan_url = None
    # XXX
    return posts, next_scan_url


class GooglePlusPost(models.Migratable):
  """A post. The key name is 'POST_ID MIGRATION_KEY_NAME'."""

  TYPE = 'post'

  def to_activity(self):
    """Returns an ActivityStreams activity dict for this post."""
    return as_googleplus.GooglePlus(None).post_to_activity(self.data())

  def get_comments(self):
    """Returns an iterable of Reply instances for replies to this post."""
    # TODO: need to do a search for this, bridgy style. :/
    replies = self.data().get('replies', {}).get('data', [])
    migration_key = Post.migration.get_value_for_datastore(self)
    return (Reply(key_name_parts=(r['id'], migration_key.name()),
                  json_data=json.dumps(r))
            for r in replies)


class GooglePlusComment(Post):
  """A comment. The key name is 'COMMENT_ID MIGRATION_KEY_NAME'."""

  TYPE = 'comment'


class AddGooglePlus(webapp2.RequestHandler):
  """Starts three-legged OAuth with Google+.

  Fetches an OAuth request token, then redirects to Google+'s auth page to
  request an access token.
  """
  @oauth.oauth_required
  def post(self):
    # get the current user's Google+ id
    try:
      me = json_service.people().get(userId='me').execute(oauth.http())
    except HttpError:
      logging.exception('Error calling People.get("me")')
      self.redirect('/?msg=%s' % urllib.quote('Error accessing Google+ for this account.'))
      return

    logging.debug('Got one person: %r' % me)

    gp = GooglePlus.new(self, token_key=access_token.key,
                        token_secret=access_token.secret)
    self.redirect('/?dest=%s&source=%s' % (self.request.get('dest'),
                                           urllib.quote(str(gp.key()))))

    #
    credentials = StorageByKeyName(CredentialsModel, user.gae_user_id,
                                   'credentials').get()
    if not credentials:
      logging.warning('Credentials not found for user id %s', user.gae_user_id)
      self.error(403)
      return

    # fetch the json stream and convert it to atom
    stream = json_service.activities().list(userId='me', collection='stream')\
        .execute(credentials.authorize(httplib2.Http()))



application = webapp2.WSGIApplication([
    ('/googleplus/source/add', AddGooglePlus),
    (oauth.callback_path, oauth.callback_handler()),
#    ('/googleplus/oauth2callback', OAuthCallback),
    ], debug=appengine_config.DEBUG)
