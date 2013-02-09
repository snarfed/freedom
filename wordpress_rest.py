"""WordPress REST API destination.
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
from webutil import util
from webutil import webapp2

from google.appengine.api import urlfetch
from google.appengine.ext import db


# https://developer.wordpress.com/apps/
WPCOM_CLIENT_ID = read('wordpress.com_client_id')
WPCOM_CLIENT_SECRET = read('wordpress.com_client_secret')


class WordPress(models.Destination):
  """A WordPress blog. The key name is the XML-RPC URL."""

  oauth_token = db.StringProperty(required=True)
  oauth_token_secret = db.StringProperty(required=True)

  def display_name(self):
    return util.domain_from_link(self.xmlrpc_url())

  @classmethod
  def new(cls, handler):
    """Creates and saves a WordPress entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler

    Returns: WordPress
    """
    properties = dict(handler.request.params)

    xmlrpc_url = properties['xmlrpc_url']
    db.LinkProperty().validate(xmlrpc_url)

    if 'blog_id' not in properties:
      properties['blog_id'] = 0

    assert 'username' in properties
    assert 'password' in properties

    return WordPress.get_or_insert(xmlrpc_url, **properties)

  def publish_post(self, post):
    """Publishes a post.

    Args:
      post: post entity

    Returns: string, the WordPress post id
    """
    # TODO: expose as option
    # Attach these tags to the WordPress posts.
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
    # http://codex.wordpress.org/XML-RPC_WordPress_API/Posts#wp.newPost
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
      # http://codex.wordpress.org/XML-RPC_WordPress_API/Categories_%26_Tags
      'terms_names': {'post_tag': POST_TAGS},
      }
    logging.info('Sending newPost: %r', new_post_params)
    post_id = xmlrpc.new_post(new_post_params)
    return str(post_id)

  def publish_comment(self, comment):
    """Publishes a comment.

    Args:
      comment: comment entity

    Returns: string, the WordPress comment id
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
class AddWordPress(webapp2.RequestHandler):
  def post(self):
    t = tumblpy.Tumblpy(app_key=WORDPRESS_APP_KEY,
                        app_secret=WORDPRESS_APP_SECRET,
                        callback_url=)

    wp = WordPress.new(self)
    wp.save()
    self.redirect('/?dest=%s' % urllib.quote(str(wp.key())))

    # # store the request token for later use in the callback handler
    # OAuthRequestToken(key_name=auth.request_token.key,
    #                   token_secret=auth.request_token.secret).put()
    # logging.info('Generated request token, redirecting to Twitter: %s', auth_url)
    # self.redirect(auth_url)


class DeleteWordPress(webapp2.RequestHandler):
  def post(self):
    site = WordPress.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


class OAuthCallback(webapp2.RequestHandler):
  """The OAuth callback. Fetches an access token and redirects to the front page."""
  def get(self):
    oauth_token = self.request.get('oauth_token', None)
    oauth_verifier = self.request.get('oauth_verifier', None)
    if oauth_token is None:
      raise exc.HTTPBadRequest('Missing required query parameter oauth_token.')

    # Lookup the request token
    request_token = OAuthRequestToken.get_by_key_name(oauth_token)
    if request_token is None:
      raise exc.HTTPBadRequest('Invalid oauth_token: %s' % oauth_token)

    # Rebuild the auth handler
    auth = tweepy.OAuthHandler(appengine_config.TWITTER_APP_KEY,
                               appengine_config.TWITTER_APP_SECRET)
    auth.set_request_token(request_token.token_key(), request_token.token_secret)

    # Fetch the access token
    try:
      access_token = auth.get_access_token(oauth_verifier)
    except tweepy.TweepError, e:
      msg = 'Twitter OAuth error, could not get access token: '
      logging.exception(msg)
      raise exc.HTTPInternalServerError(msg + `e`)

    tw = Twitter.new(self, token_key=access_token.key,
                     token_secret=access_token.secret)
    self.redirect('/?dest=%s&source=%s' % (self.request.get('dest'),
                                           urllib.quote(str(tw.key()))))


application = webapp2.WSGIApplication([
    ('/wordpress_rest/dest/add', AddWordPress),
    ('/wordpress_rest/dest/delete', DeleteWordPress),
    ('/wordpress_rest/oauth_callback', OauthCallback),
    ], debug=appengine_config.DEBUG)
