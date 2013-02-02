"""WordPress API code and datastore model classes.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']


import logging
import re
import xmlrpclib
import urllib

import appengine_config
import models
from webutil import util
from webutil import webapp2

from google.appengine.api import urlfetch
from google.appengine.ext import db


class WordPress(models.Destination):
  """A WordPress blog. Keys are id-based (ie don't have key names).

  The key name is the XML-RPC URL.

  # The key name is 'XML-RPC_URL BLOG_ID USERNAME', e.g. 'http://my.site/ 0 ryan'.
  """

  TYPE_NAME = 'WordPress'

  # xmlrpc_url = db.StringProperty(required=True)
  blog_id = db.IntegerProperty(required=True)
  username = db.StringProperty(required=True)
  password = db.StringProperty(required=True)

  def xmlrpc_url(self):
    """Returns the string XML-RPC URL."""
    return self.key_name_parts()[0]

  # def blog_id(self):
  #   """Returns the integer blog id."""
  #   return self.key_name_parts()[1]

  # def username(self):
  #   """Returns the string username."""
  #   return self.key_name_parts()[2]

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
      post: dict, parsed JSON of ActivityStreams activity
    """
    date = util.parse_iso8601(post['published'])
    location = post.get('location')
    image = post.get('image', {}).get('url', '')
  
    # extract title
    title = post.get('title')
    if not title:
      first_phrase = re.search('^[^,.:;?!]+', post.get('content', ''))
      if first_phrase:
        title = first_phrase.group()
      elif location and 'displayName' in location:
        title = 'At ' + location['displayName']
      else:
        title = date.date().isoformat()
  
    # photo
    upload = None
    if post.get('objectType') == 'photo' and image:
      logging.info('Downloading %s', image)
      resp = urllib2.urlopen(image)
      filename = os.path.basename(urlparse.urlparse(image).path)
      mime_type = resp.info().gettype()
      logging.info('Uploading as %s', mime_type)
      upload = xmlrpc.upload_file(filename, mime_type, resp.read())
  
    content = render(post, upload)
  
    # post!
    logging.info('Publishing %s', title)
    post_id = xmlrpc.new_post({
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
      })

    for comment in post.get('replies', {}).get('items', []):
      # TODO
      logging.info('Publishing comment %s', comment['id'])

  def publish_comment(self, comment):
    """Publishes a comment.

    Args:
      comment: dict, parsed JSON of ActivityStreams activity
    """
    author = reply.get('author', {})
    content = reply.get('content')
    if not content:
      continue
    logging.info('Publishing reply "%s"', content[:30])

    try:
      comment_id = xmlrpc.new_comment(post_id, {
          'author': author.get('displayName', 'Anonymous'),
          'author_url': author.get('url'),
          'content': render(reply),
          })
    except xmlrpclib.Fault, e:
      # if it's a dupe, we're done!
      if not (e.faultCode == 500 and
              e.faultString.startswith('Duplicate comment detected')):
        raise

    published = reply.get('published')
    if published:
      date = parse_iso8601(published)
      logging.info("Updating reply's time to %s", date)
      xmlrpc.edit_comment(comment_id, {'date_created_gmt': date})


# TODO: unify with other dests, sources?
class AddWordPress(webapp2.RequestHandler):
  def post(self):
    # try:
    #   wp = WordPress.new(self).save()
    # except db.BadValueError, e:
    #   # self.messages.append(str(e))
    #   pass
    wp = WordPress.new(self)
    wp.save()
    self.redirect('/?dest=%s' % urllib.quote(str(wp.key())))


class DeleteWordPress(webapp2.RequestHandler):
  def post(self):
    site = WordPress.get(self.request.params['id'])
    # TODO: remove tasks, etc.
    msg = 'Deleted %s: %s' % (site.type_display_name(), site.display_name())
    site.delete()
    self.redirect('/?msg=' + msg)


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

  transport = None

  def __init__(self, xmlrpc_url, blog_id, username, password, verbose=0):
    self.proxy = xmlrpclib.ServerProxy(xmlrpc_url, allow_none=True,
                                       transport=XmlRpc.transport, verbose=verbose)
    self.blog_id = blog_id
    self.username = username
    self.password = password

  def new_post(self, content):
    """Adds a new post.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Posts#wp.newPost

    Args:
      content: dict, see link above for fields

    Returns: string, the post id
    """
    return self.proxy.wp.newPost(self.blog_id, self.username, self.password,
                                 content)

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

  def edit_comment(self, comment_id, comment):
    """Edits an existing comment.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Comments#wp.editComment

    Args:
      comment_id: integer, comment id
      comment: dict, see link above for fields
    """
    return self.proxy.wp.editComment(self.blog_id, self.username, self.password,
                                     comment_id, comment)

  def upload_file(self, filename, mime_type, data):
    """Uploads a file.

    Details: http://codex.wordpress.org/XML-RPC_WordPress_API/Media#wp.uploadFile

    Args:
      filename: string
      mime_type: string
      data: string, the file contents (may be binary)
    """
    return self.proxy.wp.uploadFile(
      self.blog_id, self.username, self.password,
      {'name': filename, 'type': mime_type, 'bits': xmlrpclib.Binary(data)})


application = webapp2.WSGIApplication([
    ('/wordpress/dest/add', AddWordPress),
    ('/wordpress/dest/delete', DeleteWordPress),
    ], debug=appengine_config.DEBUG)
