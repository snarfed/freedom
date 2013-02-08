"""Tumblr API code and datastore model classes.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import functools
import logging
import os
import re
import sys
import xmlrpclib
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


# http://www.tumblr.com/oauth/apps
TUMBLR_APP_KEY = read('tumblr_app_key')
TUMBLR_APP_SECRET = read('tumblr_app_secret')


class Tumblr(models.Destination):
  """A Tumblr blog. The key name is the XML-RPC URL."""

  blog_id = db.IntegerProperty(required=True)
  username = db.StringProperty(required=True)
  password = db.StringProperty(required=True)

  def xmlrpc_url(self):
    """Returns the string XML-RPC URL."""
    return self.key_name_parts()[0]

  def display_name(self):
    return util.domain_from_link(self.xmlrpc_url())

  @classmethod
  def new(cls, handler):
    """Creates and saves a Tumblr entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler

    Returns: Tumblr

    Raises: BadValueError if url or xmlrpc_url are bad
    """
    properties = dict(handler.request.params)

    xmlrpc_url = properties['xmlrpc_url']
    db.LinkProperty().validate(xmlrpc_url)

    if 'blog_id' not in properties:
      properties['blog_id'] = 0

    assert 'username' in properties
    assert 'password' in properties

    return Tumblr.get_or_insert(xmlrpc_url, **properties)

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
class AddTumblr(webapp2.RequestHandler):
  def post(self):
    wp = Tumblr.new(self)
    wp.save()
    self.redirect('/?dest=%s' % urllib.quote(str(wp.key())))


class DeleteTumblr(webapp2.RequestHandler):
  def post(self):
    site = Tumblr.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


application = webapp2.WSGIApplication([
    ('/tumblr/dest/add', AddTumblr),
    ('/tumblr/dest/delete', DeleteTumblr),
    ], debug=appengine_config.DEBUG)
