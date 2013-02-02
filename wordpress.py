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

# can't 'import activitystreams' or 'import activitystreams as ...' or
# 'from activitystreams import activitystreams'. all break future importing
# inside activitystreams. damn symlinks.
STATE: this is still broken. rename symlink to activitystreams_unofficial?
from activitystreams.activitystreams import render_html 


class WordPress(models.Destination):
  """A WordPress blog. Keys are id-based (ie don't have key names).

  The key name is the XML-RPC URL.
  """

  TYPE_NAME = 'WordPress'

  blog_id = db.IntegerProperty(required=True)
  username = db.StringProperty(required=True)
  password = db.StringProperty(required=True)

  def xmlrpc_url(self):
    """Returns the string XML-RPC URL."""
    return self.key_name_parts()[0]

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
    """
    obj = post.to_activity()['object']
    date = util.parse_iso8601(obj['published'])
    location = obj.get('location')

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
      filename = os.path.basename(urlparse.urlparse(image_url).path)
      mime_type = resp.info().gettype()
      logging.info('Uploading as %s', mime_type)
      upload = xmlrpc.upload_file(filename, mime_type, resp.read())
      image['url'] = upload['url']

    content = render_html(post, upload)

    # post!
    logging.info('Publishing post %s', post['id'])
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

    for comment in obj.get('replies', {}).get('items', []):
      # TODO
      logging.info('Publishing comment %s', comment['id'])

  def publish_comment(self, comment):
    """Publishes a comment.

    Args:
      comment: dict, parsed JSON ActivityStreams object
    """
    obj = comment.to_activity()['object']
    author = obj.get('author', {})
    content = obj.get('content')
    if not content:
      logging.warning('Skipping empty comment %s', obj['id'])
      return

    logging.info('Publishing comment %s', obj['id'])

    try:
      comment_id = xmlrpc.new_comment(post_id, {
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
      date = parse_iso8601(published)
      logging.info("Updating comment's time to %s", date)
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
