"""Blogger destination.

Uses the v2 GData API, *not* the v3 REST API, which can't create comments. More:
https://groups.google.com/d/topic/bloggerdev/jRJKC7jjs6M/discussion
https://developers.google.com/blogger/docs/3.0/reference/comments
https://developers.google.com/blogger/docs/2.0/developers_guide

Uses google-api-python-client to auth via OAuth 2. The integration between
gdata-python-client and google-api-python-client was added here:
https://code.google.com/p/gdata-python-client/source/detail?r=ecb1d49b5fbe05c9bc6c8525e18812ccc02badc0
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

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

from apiclient import discovery
from apiclient.errors import HttpError
from oauth2client.appengine import CredentialsModel
from oauth2client.appengine import OAuth2Decorator
from oauth2client.appengine import StorageByKeyName
from google.appengine.api import users
from google.appengine.ext import db


# service names and versions:
# https://developers.google.com/api-client-library/python/reference/supported_apis
json_service = discovery.build('blogger', 'v3')
oauth = OAuth2Decorator(
  client_id=appengine_config.GOOGLEPLUS_CLIENT_ID,
  client_secret=appengine_config.GOOGLEPLUS_CLIENT_SECRET,
  # https://developers.google.com/blogger/docs/3.0/using#OAuth2Scope
  scope='https://www.googleapis.com/auth/blogger',
  callback_path='/blogger/oauth2callback')


class Blogger(models.Destination):
  """A Blogger blog. The key name is the blog id."""

  domain = db.StringProperty(required=True)
  # the App Engine user id, ie users.get_current_user().user_id()
  gae_user_id = db.StringProperty(required=True)

  def display_name(self):
    return self.domain

  @classmethod
  def new(cls, handler):
    """Creates and saves a Blogger entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler

    Returns: Blogger
    """
    properties = dict(handler.request.params)

    xmlrpc_url = properties['xmlrpc_url']
    db.LinkProperty().validate(xmlrpc_url)

    if 'blog_id' not in properties:
      properties['blog_id'] = 0

    assert 'username' in properties
    assert 'password' in properties

    return Blogger.get_or_insert(xmlrpc_url, **properties)

  def publish_post(self, post):
    """Publishes a post.

    Args:
      post: post entity

    Returns: string, the Blogger post id
    """
    # TODO: expose as option
    # Attach these tags to the Blogger posts.
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
    # http://codex.blogger.org/XML-RPC_Blogger_API/Posts#wp.newPost
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
      # http://codex.blogger.org/XML-RPC_Blogger_API/Categories_%26_Tags
      'terms_names': {'post_tag': POST_TAGS},
      }
    logging.info('Sending newPost: %r', new_post_params)
    post_id = xmlrpc.new_post(new_post_params)
    return str(post_id)

  def publish_comment(self, comment):
    """Publishes a comment.

    Args:
      comment: comment entity

    Returns: string, the Blogger comment id
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


class AddBlogger(webapp2.RequestHandler):
  """Adds a Blogger account. Authenticates via OAuth if necessary."""
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
    self.redirect('/?dest=%s&source=%s' % (self.request.get('dest'),
                                           str(gp.key())))


# TODO: unify with other dests, sources?
class AddBlogger(webapp2.RequestHandler):
  def post(self):
    wp = Blogger.new(self)
    wp.save()
    self.redirect('/?dest=%s' % str(wp.key()))


class DeleteBlogger(webapp2.RequestHandler):
  def post(self):
    site = Blogger.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


application = webapp2.WSGIApplication([
    ('/blogger/dest/add', AddBlogger),
    ('/blogger/dest/delete', DeleteBlogger),
    ], debug=appengine_config.DEBUG)
