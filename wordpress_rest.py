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
from webutil import webapp2

from google.appengine.api import urlfetch
from google.appengine.ext import db


# https://developer.wordpress.com/docs/api/1/
API_ME_URL = 'https://public-api.wordpress.com/rest/v1/me/'
API_SITE_URL = 'https://public-api.wordpress.com/rest/v1/sites/%d'


# https://developer.wordpress.com/apps/2043/
if appengine_config.DEBUG:
  CLIENT_ID = appengine_config.read('wordpress.com_client_id_local')
  CLIENT_SECRET = appengine_config.read('wordpress.com_client_secret_local')
else:
  CLIENT_ID = appengine_config.read('wordpress.com_client_id')
  CLIENT_SECRET = appengine_config.read('wordpress.com_client_secret')

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
  callback_path='/wordpress_rest/oauth_callback')


class WordPressRest(models.Destination):
  """A WordPress blog accessed via REST API. Currently only wordpress.com."""

  oauth_token = db.StringProperty(required=True)
  oauth_token_secret = db.StringProperty(required=True)

  def display_name(self):
    return util.domain_from_link(self.xmlrpc_url())

  @classmethod
  def new(cls, handler):
    """Creates and saves a WordPressRest entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler

    Returns: WordPressRest
    """
    properties = dict(handler.request.params)

    xmlrpc_url = properties['xmlrpc_url']
    db.LinkProperty().validate(xmlrpc_url)

    if 'blog_id' not in properties:
      properties['blog_id'] = 0

    assert 'username' in properties
    assert 'password' in properties

    return WordPressRest.get_or_insert(xmlrpc_url, **properties)

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
class AddWordPressRest(webapp2.RequestHandler):
  @oauth.oauth_required
  def get(self):
    self.post()

  @oauth.oauth_required
  def post(self):
    # TODO: the HTTP request that gets an access token also gets the blog id and
    # url selected by the user:
    # https://developer.wordpress.com/docs/oauth2/#exchange-code-for-access-token
    # ideally i'd use that instead of requesting it manually, but it's not
    # exposed. :/
    # STATE: actually i do really need this, since it's the only way to see
    # which blog the user selected. sigh. asked joe gregorio here:
    # https://plus.google.com/106134299616714031548/posts/XtuGWPzG3Rc
    http = oauth.http()
    resp, content = http.request(API_ME_URL)
    assert resp.status == 200
    me = json.loads(content)
    logging.debug('WPCOM me: %s' % me)

    resp, content = http.request(API_SITE_URL % me['primary_blog'])
    assert resp.status == 200
    logging.debug('WPCOM site: %s' % content)

    wpr = WordPressRest.new(self)
    # redirect so that refreshing the page doesn't try to rewrite the Blogger
    # entity.
    self.redirect('/?dest=%s' % str(wpr.key()))


class DeleteWordPressRest(webapp2.RequestHandler):
  def post(self):
    site = WordPressRest.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


application = webapp2.WSGIApplication([
    (oauth.callback_path, oauth.callback_handler()),
    ('/wordpress_rest/dest/add', AddWordPressRest),
    ('/wordpress_rest/dest/delete', DeleteWordPressRest),
    ], debug=appengine_config.DEBUG)
