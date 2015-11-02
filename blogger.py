"""Blogger destination.

Uses the v2 GData API:
https://developers.google.com/blogger/docs/2.0/developers_guider

Does *not* use the v3 REST API because it can't create comments. More:
https://groups.google.com/d/topic/bloggerdev/jRJKC7jjs6M/discussion
https://developers.google.com/blogger/docs/3.0/reference/comments

Uses google-api-python-client to auth via OAuth 2. This describes how to get
gdata-python-client to use an OAuth 2 token from google-api-python-client:
http://blog.bossylobster.com/2012/12/bridging-oauth-20-objects-between-gdata.html#comment-form

Support was added to gdata-python-client here:
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

from oauth2client.appengine import OAuth2Decorator
from gdata.blogger import client
from gdata import gauth
from google.appengine.api import users
from google.appengine.ext import db
import webapp2


oauth = OAuth2Decorator(
  client_id=appengine_config.GOOGLE_CLIENT_ID,
  client_secret=appengine_config.GOOGLE_CLIENT_SECRET,
  # https://developers.google.com/blogger/docs/2.0/developers_guide_protocol#OAuth2Authorizing
  # (the scope for the v3 API is https://www.googleapis.com/auth/blogger)
  scope='http://www.blogger.com/feeds/',
  callback_path='/blogger/oauth2callback')


class Blogger(models.Destination):
  """A Blogger blog. The key name is the blog hostname."""

  owner_name = db.StringProperty(required=True)
  # the App Engine user id, ie users.get_current_user().user_id()
  gae_user_id = db.StringProperty(required=True)

  def hostname(self):
    return self.key().name()

  def display_name(self):
    return self.hostname()

  @classmethod
  def new(cls, handler, **kwargs):
    """Creates and saves a Blogger entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler
      kwargs: passed through to the Blogger() constructor

    Returns: Blogger
    """
    return Blogger.get_or_insert(
      handler.request.get('host'),
      owner_name=handler.request.get('blogger_owner_name'),
      gae_user_id=users.get_current_user().user_id(),
      **kwargs)

  def publish_post(self, post):
    """Publishes a post.

    Args:
      post: post entity

    Returns: string, the Blogger post id
    """
    # TODO: expose as option
    # Attach these tags to the Blogger posts.
    POST_TAGS = ['Freedom']

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

    # post!
    # http://codex.blogger.org/XML-RPC_Blogger_API/Posts#wp.newPost
    new_post_params = {
      'post_type': 'post',
      'post_status': 'publish',
      'post_title': title,
      # leave this unset to default to the authenticated user
      # 'post_author': 0,
      'post_content': post.render_html(),
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
          'content': comment.render_html(),
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


class ConnectBlogger(webapp2.RequestHandler):
  """Connects a Blogger account. Authenticates via OAuth if necessary."""
  @oauth.oauth_required
  def post(self):
    return self.get()

  @oauth.oauth_required
  def get(self):
    # this must be a client ie subclass of GDClient, since that's what
    # OAuth2TokenFromCredentials.authorize() expects, *not* a service ie
    # subclass of GDataService.
    blogger = client.BloggerClient()
    auth_token = gauth.OAuth2TokenFromCredentials(oauth.credentials)
    auth_token.authorize(blogger)

    # get the current user
    blogs = blogger.get_blogs()
    logging.debug('Got blogs: %r' % str(blogs))
    owner_name = blogs.entry[0].author[0].name.text if blogs.entry else None
    hostnames = []
    for entry in blogs.entry:
      for link in entry.link:
        if link.type == 'text/html':
          domain = util.domain_from_link(link.href)
          if domain:
            hostnames.append(domain)
            break

    # redirect so that refreshing the page doesn't try to regenerate the oauth
    # token, which won't work.
    self.redirect('/?' + urllib.urlencode({
          'blogger_owner_name': owner_name,
          'blogger_hostnames': hostnames,
          }, True))


# TODO: unify with other dests, sources?
class AddBlogger(webapp2.RequestHandler):
  def post(self):
    bl = Blogger.new(self)
    # redirect so that refreshing the page doesn't try to rewrite the Blogger
    # entity.
    self.redirect('/?dest=%s#sources' % str(bl.key()))


class DeleteBlogger(webapp2.RequestHandler):
  def post(self):
    site = Blogger.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


application = webapp2.WSGIApplication([
    ('/blogger/dest/connect', ConnectBlogger),
    (oauth.callback_path, oauth.callback_handler()),
    ('/blogger/dest/add', AddBlogger),
    ('/blogger/dest/delete', DeleteBlogger),
    ], debug=appengine_config.DEBUG)
