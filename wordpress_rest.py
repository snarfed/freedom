"""WordPress.com REST API destination.

Note that unlike Blogger and Tumblr, WordPress.com's OAuth tokens are *per
blog*. It asks you which blog to use On its authorization page. So, I don't need
the extra handler and form in index.html to choose between blogs.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import functools
import json
import logging
import os
import re
import urllib
import urllib2
import urlparse

from activitystreams import activitystreams
import appengine_config
import models
from oauth2client.appengine import OAuth2Decorator
from webutil import util

from google.appengine.api import urlfetch
from google.appengine.ext import db
import webapp2


# https://developer.wordpress.com/docs/api/1/
API_ME_URL = 'https://public-api.wordpress.com/rest/v1/me/'
API_SITE_URL = 'https://public-api.wordpress.com/rest/v1/sites/%d'


CALLBACK_PATH = '/wordpress_rest/oauth_callback'
if appengine_config.DEBUG:
  # https://developer.wordpress.com/apps/2090/
  CLIENT_ID = appengine_config.read('wordpress.com_client_id_local')
  CLIENT_SECRET = appengine_config.read('wordpress.com_client_secret_local')
  CALLBACK_URL = 'http://my.dev.com:8080' + CALLBACK_PATH
else:
  # https://developer.wordpress.com/apps/2043/
  CLIENT_ID = appengine_config.read('wordpress.com_client_id')
  CLIENT_SECRET = appengine_config.read('wordpress.com_client_secret')
  CALLBACK_URL = CALLBACK_PATH


TOKEN_RESPONSE_PARAM = 'token_response'
oauth = OAuth2Decorator(
  client_id=CLIENT_ID,
  client_secret=CLIENT_SECRET,
  # can't find any mention of oauth scope in https://developer.wordpress.com/
  scope='',
  auth_uri='https://public-api.wordpress.com/oauth2/authorize',
  token_uri='https://public-api.wordpress.com/oauth2/token',
  # wordpress.com doesn't let you use an oauth redirect URL with "local" or
  # "localhost" anywhere in it. :/ had to use my.dev.com and put this in
  # /etc/hosts:   127.0.0.1 my.dev.com
  callback_path=CALLBACK_URL,
  # the HTTP request that gets an access token also gets the blog id and
  # url selected by the user, so grab it from the token response.
  token_response_param=TOKEN_RESPONSE_PARAM)


class WordPressRest(models.Destination):
  """A WordPress blog accessed via REST API. The key name is the blog hostname.

  Currently only supports wordpress.com.
  """

  blog_id = db.StringProperty(required=True)
  oauth_token = db.StringProperty()#required=True)
  oauth_token_secret = db.StringProperty()#required=True)

  def hostname(self):
    return self.key().name()

  def display_name(self):
    return self.hostname()

  @classmethod
  def new(cls, handler, blog_id, blog_url):
    """Creates and saves a WordPressRest entity.

    Args:
      handler: the current webapp.RequestHandler
      blog_id: string
      blog_url: string

    Returns: WordPressRest
    """
    properties = dict(handler.request.params)
    key_name = util.domain_from_link(blog_url)
    return WordPressRest.get_or_insert(key_name, blog_id=blog_id, **properties)

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

    # post!
    # http://codex.wordpress.org/XML-RPC_WordPress_API/Posts#wp.newPost
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


# TODO: unify with other dests, sources?
class AddWordPressRest(webapp2.RequestHandler):
  @oauth.oauth_required
  def get(self):
    self.post()

  @oauth.oauth_required
  def post(self):

    # the HTTP request that gets an access token also gets the blog id and
    # url selected by the user, so grab it from the token response.
    # https://developer.wordpress.com/docs/oauth2/#exchange-code-for-access-token
    token_resp = self.request.get(TOKEN_RESPONSE_PARAM)
    try:
      resp = json.loads(token_resp)
      wpr = WordPressRest.new(self, resp['blog_id'], resp['blog_url'])
    except:
      logging.error('Bad JSON response: %r', self.request.body)
      raise

    # redirect so that refreshing the page doesn't try to rewrite the Blogger
    # entity.
    self.redirect('/?dest=%s#sources' % str(wpr.key()))


class DeleteWordPressRest(webapp2.RequestHandler):
  def post(self):
    site = WordPressRest.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


application = webapp2.WSGIApplication([
    (CALLBACK_PATH, oauth.callback_handler()),
    ('/wordpress_rest/dest/add', AddWordPressRest),
    ('/wordpress_rest/dest/delete', DeleteWordPressRest),
    ], debug=appengine_config.DEBUG)
