"""Tumblr destination.
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
from webutil import util
from webutil import webapp2

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext.webapp import template


# http://www.tumblr.com/oauth/apps
TUMBLR_APP_KEY = appengine_config.read('tumblr_app_key')
TUMBLR_APP_SECRET = appengine_config.read('tumblr_app_secret')

OAUTH_CALLBACK_URL = '%s://%s/tumblr/oauth_callback' % (
  appengine_config.SCHEME, appengine_config.HOST)
# API_USER_INFO_URL = 'http://api.tumblr.com/v2/user/info'


class TumblrOAuthRequestToken(models.OAuthToken):
  pass


class TumblrOAuthFinalToken(models.OAuthToken):
  pass


class Tumblr(models.Destination):
  """A Tumblr blog. The key name is the hostname."""

  username = db.StringProperty(required=True)
  # title = db.StringProperty(required=True)

  # Tumblr OAuth 1.0A access token for this account
  # http://www.tumblr.com/docs/en/api/v2#auth
  token_key = db.StringProperty(required=True)
  token_secret = db.StringProperty(required=True)

  def display_name(self):
    return self.key().name()

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
    POST_TAGS = ['freedom.io']

    activity = post.to_activity()
    obj = activity['object']
    date = util.parse_iso8601(activity['published'])
    location = obj.get('location')
    xmlrpc = XmlRpc(self.xmlrpc_url(), self.blog_id, self.username, self.password,
                    verbose=True, transport=GAEXMLRPCTransport())
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

    # photo
    image = obj.get('image', {})
    image_url = image.get('url')
    if obj.get('objectType') == 'photo' and image_url:
      logging.info('Downloading %s', image_url)
      resp = urllib2.urlopen(image_url)
      data = resp.read()
      logging.debug('downloaded %d bytes', len(data))
      filename = os.path.basename(urlparse.urlparse(image_url).path)
      mime_type = resp.info().gettype()
      logging.info('Sending uploadFile: %s %s', mime_type, filename)
      upload = xmlrpc.upload_file(filename, mime_type, data)
      image['url'] = upload['url']

    content = activitystreams.render_html(obj)

    # post!
    # http://codex.tumblr.org/XML-RPC_Tumblr_API/Posts#wp.newPost
    new_post_params = {
      'post_type': 'post',
      'post_status': 'publish',
      'post_title': title,
      # leave this unset to default to the authenticated user
      # 'post_author': 0,
      'post_content': content,
      'post_date': date,
      'comment_status': 'open',
      # WP post tags are now implemented as taxonomies:
      # http://codex.tumblr.org/XML-RPC_Tumblr_API/Categories_%26_Tags
      'terms_names': {'post_tag': POST_TAGS},
      }
    logging.info('Sending newPost: %r', new_post_params)
    post_id = xmlrpc.new_post(new_post_params)
    return str(post_id)

  def publish_comment(self, comment):
    """Publishes a comment.

    Args:
      comment: comment entity

    Returns: string, the Tumblr comment id
    """
    obj = comment.to_activity()['object']
    author = obj.get('author', {})
    content = obj.get('content')
    if not content:
      logging.warning('Skipping empty comment %s', obj['id'])
      return

    logging.info('Publishing comment %s', obj['id'])
    xmlrpc = XmlRpc(self.xmlrpc_url(), self.blog_id, self.username, self.password,
                    verbose=True, transport=GAEXMLRPCTransport())

    try:
      comment_id = xmlrpc.new_comment(comment.dest_post_id, {
          'author': author.get('displayName', 'Anonymous'),
          'author_url': author.get('url'),
          'content': activitystreams.render_html(obj),
          })
    except xmlrpclib.Fault, e:
      # if it's a dupe, we're done!
      if not (e.faultCode == 500 and
              e.faultString.startswith('Duplicate comment detected')):
        raise

    published = obj.get('published')
    if published:
      date = util.parse_iso8601(published)
      logging.info("Updating comment's time to %s", date)
      xmlrpc.edit_comment(comment_id, {'date_created_gmt': date})

    return str(comment_id)


# TODO: unify with other dests, sources?
class ConnectTumblr(webapp2.RequestHandler):
  def post(self):
    tp = tumblpy.Tumblpy(app_key=TUMBLR_APP_KEY,
                         app_secret=TUMBLR_APP_SECRET,
                         callback_url=OAUTH_CALLBACK_URL)
    auth_props = tp.get_authentication_tokens()

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
    tp = tumblpy.Tumblpy(app_key=TUMBLR_APP_KEY,
                         app_secret=TUMBLR_APP_SECRET,
                         oauth_token=final_token,
                         oauth_token_secret=final_secret)
    resp = tp.post('user/info')
    logging.debug(resp)
    user = resp['user']
    hostnames = [util.domain_from_link(b['url']) for b in user['blogs']]
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

    tumblr = Tumblr.new(self,
                        # title=self.request.get('title'),
                        token_key=token.token_key(),
                        token_secret=token.secret)

    # redirect so that refreshing the page doesn't try to regenerate the oauth
    # token, which won't work.
    self.redirect('/?dest=%s' % str(tumblr.key()))


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
