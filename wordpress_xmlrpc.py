"""WordPress XML-RPC destination.
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


class WordPress(models.Destination):
  """A WordPress blog. The key name is the XML-RPC URL."""

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
    """Creates and saves a WordPress entity based on query parameters.

    Args:
      handler: the current webapp.RequestHandler

    Returns: WordPress

    Raises: BadValueError if url or xmlrpc_url are bad
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
class AddWordPress(webapp2.RequestHandler):
  def post(self):
    wp = WordPress.new(self)
    wp.save()
    self.redirect('/?dest=%s#sources' % str(wp.key()))


class DeleteWordPress(webapp2.RequestHandler):
  def post(self):
    site = WordPress.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


def stdout_on(fn):
  """A decorator that resets stdout temporarily for the duration of the function.

  xmlrpclib uses stdout for its verbose output, but dev_appserver and HTTP
  handlers both intercept and reroute it, so the verbose output is lost. This
  fixes that.
  """
  @functools.wraps(fn)
  def wrapper(*args, **kwds):
    orig = sys.stdout
    sys.stdout = sys.__stdout__
    try:
      return fn(*args, **kwds)
    finally:
      sys.stdout = orig

  return wrapper


class XmlRpc(object):
  """A minimal XML-RPC interface to a WordPress blog.

  Details: http://codex.wordpress.org/XML-RPC_WordPress_API

  TODO: error handling

  Class attributes:
    transport: Transport instance passed to ServerProxy()

  Attributes:
    proxy: xmlrpclib.ServerProxy
    blog_id: integer
    username: string, may be None
    password: string, may be None
  """

  def __init__(self, url, blog_id, username, password, verbose=0, transport=None):
    # xmlrpclib is finicky about the url parameter. it doesn't allow unicode, so
    # coerce to string.
    self.proxy = xmlrpclib.ServerProxy(str(url), allow_none=True,
                                       transport=transport, verbose=verbose)
    self.blog_id = blog_id
    self.username = username
    self.password = password

  @stdout_on
  def new_post(self, content):
    """Adds a new post.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Posts#wp.newPost

    Args:
      content: dict, see link above for fields

    Returns: string, the post id
    """
    return self.proxy.wp.newPost(self.blog_id, self.username, self.password,
                                 content)

  @stdout_on
  def new_comment(self, post_id, comment):
    """Adds a new comment.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Comments#wp.newComment

    Args:
      post_id: integer, post id
      comment: dict, see link above for fields

    Returns: integer, the comment id
    """
    # *don't* pass in username and password. if you do, that wordpress user's
    # name and url override the ones we provide in the xmlrpc call.
    #
    # also, use '' instead of None, even though we use allow_none=True. it
    # converts None to <nil />, which wordpress's xmlrpc server interprets as
    # "no parameter" instead of "blank parameter."
    #
    # note that this requires anonymous commenting to be turned on in wordpress
    # via the xmlrpc_allow_anonymous_comments filter.
    return self.proxy.wp.newComment(self.blog_id, '', '', post_id, comment)

  @stdout_on
  def edit_comment(self, comment_id, comment):
    """Edits an existing comment.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Comments#wp.editComment

    Args:
      comment_id: integer, comment id
      comment: dict, see link above for fields
    """
    return self.proxy.wp.editComment(self.blog_id, self.username, self.password,
                                     comment_id, comment)

  @stdout_on
  def upload_file(self, filename, mime_type, data):
    """Uploads a file.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Media#wp.uploadFile

    Args:
      filename: string
      mime_type: string
      data: string, the file contents (may be binary)
    """
    logging.info('uploading %d bytes', len(data))
    return self.proxy.wp.uploadFile(
      self.blog_id, self.username, self.password,
      {'name': filename, 'type': mime_type, 'bits': xmlrpclib.Binary(data)})


class GAEXMLRPCTransport(object):
    """Handles an HTTP transaction to an XML-RPC server.

    From http://brizzled.clapper.org/blog/2008/08/25/making-xmlrpc-calls-from-a-google-app-engine-application/
    """

    def __init__(self):
        pass

    def request(self, host, handler, request_body, verbose=0):
        result = None
        url = 'http://%s%s' % (host, handler)
        try:
            response = urlfetch.fetch(url,
                                      payload=request_body,
                                      method=urlfetch.POST,
                                      headers={'Content-Type': 'text/xml'})
        except:
            msg = 'Failed to fetch %s' % url
            logging.error(msg)
            raise xmlrpclib.ProtocolError(host + handler, 500, msg, {})

        if response.status_code != 200:
            logging.error('%s returned status code %s' %
                          (url, response.status_code))
            raise xmlrpclib.ProtocolError(host + handler,
                                          response.status_code,
                                          "",
                                          response.headers)
        else:
            result = self.__parse_response(response.content)

        return result

    def __parse_response(self, response_body):
        p, u = xmlrpclib.getparser(use_datetime=False)
        p.feed(response_body)
        return u.close()


application = webapp2.WSGIApplication([
    ('/wordpress_xmlrpc/dest/add', AddWordPress),
    ('/wordpress_xmlrpc/dest/delete', DeleteWordPress),
    ], debug=appengine_config.DEBUG)
