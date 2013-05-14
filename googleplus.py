#!/usr/bin/python
"""Google+ source class.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import json
import httplib2
import logging
import urllib
import urlparse

# from activitystreams import googleplus as as_googleplus
import appengine_config
import models

from webutil import util
from webutil import webapp2

from apiclient import discovery
from apiclient.errors import HttpError
from oauth2client.appengine import CredentialsModel
from oauth2client.appengine import OAuth2Decorator
from oauth2client.appengine import StorageByKeyName
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext.webapp import template


# service names and versions:
# https://developers.google.com/api-client-library/python/reference/supported_apis
json_service = discovery.build('plus', 'v1')
oauth = OAuth2Decorator(
  client_id=appengine_config.GOOGLE_CLIENT_ID,
  client_secret=appengine_config.GOOGLE_CLIENT_SECRET,
  # G+ scopes: https://developers.google.com/+/api/oauth#oauth-scopes
  scope='https://www.googleapis.com/auth/plus.me',
  callback_path='/googleplus/oauth2callback')


class GooglePlus(models.Source):
  """A Google+ account. The key name is the Google+ user id."""

  DOMAIN = 'googleplus.com'

  name = db.StringProperty(required=True)
  # the App Engine user id, ie users.get_current_user().user_id()
  gae_user_id = db.StringProperty(required=True)

  def display_name(self):
    return self.name

  @staticmethod
  def new(handler, user):
    """Creates and returns a GooglePlus instance for the authenticated user.

    Args:
      handler: the current webapp2.RequestHandler
      user: dict, decoded JSON object representing the current user
    """
    return GooglePlus.get_or_insert(
      user['id'],
      gae_user_id=users.get_current_user().user_id(),
      name=user['displayName'],
      picture=user['image']['url'],
      url=user['url'])

  def get_posts(self, migration, scan_url=None):
    """Fetches a page of posts.

    Args:
      migration: Migration
      scan_url: string, the API URL to fetch the current page of posts. If None,
        starts at the beginning.

    Returns:
      (posts, next_scan_url). posts is a sequence of Migratables.
      next_scan_url is a string, the API URL to use for the next scan, or None
      if there is nothing more to scan.
    """
    # TODO: expose as options
    # https://dev.googleplus.com/docs/api/1.1/get/statuses/user_timeline

    # get this user's OAuth credentials
    credentials = StorageByKeyName(CredentialsModel, self.gae_user_id,
                                   'credentials').get()
    if not credentials:
      logging.error('Giving up: credentials not found for user id %s.',
                    self.gae_user_id)
      self.error(299)
      return

    # TODO: convert scan_url to paging param(s)
    # if not scan_url:
    #   scan_url = API_POSTS_URL % self.key().name()
    # gp = as_googleplus.GooglePlus(None)
    # resp = json.loads(gp.urlfetch(scan_url))

    # fetch the json stream and convert it to atom.
    # (if i use collection 'user' instead of 'public', that would get *all*
    # posts, not just public posts, but that's not allowed yet. :/ )
    resp = json_service.activities().list(userId='me', collection='public')\
        .execute(credentials.authorize(httplib2.Http()))

    posts = []
    for post in resp['items']:
      id = post['id']
      app = post.get('source')
      if app and app in APPLICATION_BLACKLIST:
        logging.info('Skipping post %d', id)
        continue

      posts.append(GooglePlusPost(key_name_parts=(str(id), migration.key().name()),
                                  json_data=json.dumps(post)))

    next_scan_url = None
    # if posts:
    #   scan_url + '&max_id=%s' % posts[-1].id()
    # # XXX remove
    # if posts and posts[-1].data()['created_time'] < '2013-01-01':
    #   next_scan_url = None
    # # XXX
    return posts, next_scan_url


class GooglePlusPost(models.Migratable):
  """A post. The key name is 'POST_ID MIGRATION_KEY_NAME'."""

  TYPE = 'post'

  def to_activity(self):
    """Returns an ActivityStreams activity dict for this post."""
    activity = self.data()

    # copy id into object if it's not already there
    obj = activity.get('object', {})
    if 'id' not in obj:
      obj['id'] = activity['id']

    return activity

  def get_comments(self):
    """Returns an iterable of GooglePlusComments for replies to this post."""
    # TODO: implement
    comments = self.data().get('comments', {}).get('data', [])
    migration_key = GooglePlusPost.migration.get_value_for_datastore(self)
    return (GooglePlusComment(key_name_parts=(c['id'], migration_key.name()),
                              json_data=json.dumps(r))
            for c in comments)


class GooglePlusComment(models.Migratable):
  """A comment. The key name is 'COMMENT_ID MIGRATION_KEY_NAME'."""

  TYPE = 'comment'


class AddGooglePlus(webapp2.RequestHandler):
  """Adds a Google+ account. Authenticates via OAuth if necessary."""
  @oauth.oauth_required
  def get(self):
    # get the current user
    try:
      me = json_service.people().get(userId='me').execute(oauth.http())
    except HttpError:
      logging.exception('Error calling People.get("me")')
      self.redirect('/?msg=%s' % urllib.quote('Error accessing Google+ for this account.'))
      return

    logging.debug('Got one person: %r' % me)

    gp = GooglePlus.new(self, me)

    # redirect so that refreshing doesn't rewrite this GooglePlus entity
    self.redirect('/?' + urllib.urlencode({'dest': self.request.get('dest'),
                                           'source': urllib.quote(str(gp.key()))}))


application = webapp2.WSGIApplication([
    ('/googleplus/source/add', AddGooglePlus),
    (oauth.callback_path, oauth.callback_handler()),
    ], debug=appengine_config.DEBUG)
