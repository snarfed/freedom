#!/usr/bin/python
"""Twitter source class.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import json
import logging
import urllib
import urlparse
from webob import exc

from activitystreams import twitter as as_twitter
import appengine_config
import models
import tweepy

from webutil import util
from webutil import webapp2

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext.webapp import template


OAUTH_CALLBACK = '%s://%s/twitter/oauth_callback?dest=%%s' % (appengine_config.SCHEME,
                                                              appengine_config.HOST)
API_TWEETS_URL = ('https://api.twitter.com/1.1/statuses/user_timeline.json'
                  '?include_entities=true&screen_name=%s')


class TwitterOAuthRequestToken(models.OAuthRequestToken):
  pass


class Twitter(models.Source):
  """A Twitter account. The key name is the username."""

  DOMAIN = 'twitter.com'

  # Twitter OAuth 1.0A access token for this account
  # https://dev.twitter.com/docs/auth/3-legged-authorization
  token_key = db.StringProperty()
  token_secret = db.StringProperty()

  def display_name(self):
    return self.key().name()

  @staticmethod
  def new(handler, token_key=None, token_secret=None):
    """Creates and returns a Twitter instance for the authenticated user.

    Args:
      handler: the current webapp2.RequestHandler
    """
    tw = as_twitter.Twitter(handler)
    me = tw.get_actor(access_token_key=token_key,
                      access_token_secret=token_secret)
    return Twitter.get_or_insert(
      me['username'],
      token_key=token_key,
      token_secret=token_secret,
      picture=me['image']['url'],
      url=me['url'])

  def get_posts(self, migration, scan_url=None):
    """Fetches a page of tweets.

    Args:
      migration: Migration
      scan_url: string, the API URL to fetch the current page of tweets. If None,
        starts at the beginning.

    Returns:
      (tweets, next_scan_url). tweets is a sequence of Tweets.
      next_scan_url is a string, the API URL to use for the next scan, or None
      if there is nothing more to scan.
    """
    # TODO: expose as options
    # https://dev.twitter.com/docs/api/1.1/get/statuses/user_timeline
    INCLUDE_RETWEETS = False    # ?exclude_replies=false
    INCLUDE_AT_REPLIES = False  # ?include_rts=true

    # Don't publish tweets from these applications
    APPLICATION_BLACKLIST = ('Likes', 'Links', 'twitterfeed')

    if not scan_url:
      scan_url = API_TWEETS_URL % self.key().name()
    tw = as_twitter.Twitter(None)
    resp = json.loads(tw.urlfetch(scan_url,
                                  access_token_key=self.token_key,
                                  access_token_secret=self.token_secret))

    tweets = []
    for tweet in resp:
      id = tweet['id']
      app = tweet.get('source')
      if app and app in APPLICATION_BLACKLIST:
        logging.info('Skipping tweet %d', id)
        continue

      tweets.append(Tweet(key_name_parts=(str(id), migration.key().name()),
                          json_data=json.dumps(tweet)))

    next_scan_url = None
    if tweets:
      scan_url + '&max_id=%s' % tweets[-1].id()
    # XXX remove
    if tweets and tw.rfc2822_to_iso8601(tweets[-1].data()['created_at']) < '2013--01-01':
      next_scan_url = None
    # XXX
    return tweets, next_scan_url


class Tweet(models.Migratable):
  """A tweet. The key name is 'TWEET_ID MIGRATION_KEY_NAME'."""

  TYPE = 'post'

  def to_activity(self):
    """Returns an ActivityStreams activity dict for this tweet."""
    return as_twitter.Twitter(None).tweet_to_activity(self.data())

  def get_comments(self):
    """Returns an iterable of Reply instances for replies to this tweet."""
    # TODO: need to do a search for this, bridgy style. :/
    replies = self.data().get('replies', {}).get('data', [])
    migration_key = Tweet.migration.get_value_for_datastore(self)
    return (Reply(key_name_parts=(r['id'], migration_key.name()),
                  json_data=json.dumps(r))
            for r in replies)


class Reply(Tweet):
  """A reply tweet. The key name is 'TWEET_ID MIGRATION_KEY_NAME'."""

  TYPE = 'comment'


class AddTwitter(webapp2.RequestHandler):
  """Starts three-legged OAuth with Twitter.

  Fetches an OAuth request token, then redirects to Twitter's auth page to
  request an access token.
  """
  def post(self):
    try:
      auth = tweepy.OAuthHandler(appengine_config.TWITTER_APP_KEY,
                                 appengine_config.TWITTER_APP_SECRET,
                                 OAUTH_CALLBACK % self.request.get('dest'))
      auth_url = auth.get_authorization_url()
    except tweepy.TweepError, e:
      msg = 'Could not create Twitter OAuth request token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    # store the request token for later use in the callback handler
    TwitterOAuthRequestToken.new(auth.request_token.key, auth.request_token.secret)
    logging.info('Generated request token, redirecting to Twitter: %s', auth_url)
    self.redirect(auth_url)


class OAuthCallback(webapp2.RequestHandler):
  """The OAuth callback. Fetches an access token and redirects to the front page."""
  def get(self):
    oauth_token = self.request.get('oauth_token', None)
    oauth_verifier = self.request.get('oauth_verifier', None)
    if oauth_token is None:
      raise exc.HTTPBadRequest('Missing required query parameter oauth_token.')

    # Lookup the request token
    request_token = TwitterOAuthRequestToken.get_by_key_name(oauth_token)
    if request_token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % oauth_token)

    # Rebuild the auth handler
    auth = tweepy.OAuthHandler(appengine_config.TWITTER_APP_KEY,
                               appengine_config.TWITTER_APP_SECRET)
    auth.set_request_token(request_token.token_key(), request_token.secret)

    # Fetch the access token
    try:
      access_token = auth.get_access_token(oauth_verifier)
    except tweepy.TweepError, e:
      msg = 'Twitter OAuth error, could not get access token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    tw = Twitter.new(self, token_key=access_token.key,
                     token_secret=access_token.secret)
    vars = {'dest': self.request.get('dest'),
            'source': urllib.quote(str(tw.key()))}
    self.response.out.write(template.render('templates/index.html', vars))


application = webapp2.WSGIApplication([
    ('/twitter/source/add', AddTwitter),
    ('/twitter/oauth_callback', OAuthCallback),
    ], debug=appengine_config.DEBUG)
