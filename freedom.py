#!/usr/bin/python
"""Publishes a Facebook, Twitter, or Google+ posts to WordPress via XML-RPC.

http://freedom.io/
http://snarfed.org/freedom

Usage: freedom.py XMLRPC_URL USERNAME PASSWORD < FILENAME

Reads one or more Facebook posts from stdin, in Graph API JSON representation,
and publishes them to a WordPress blog via XML-RPC. Includes attached images,
locations (ie checkins), links, tagged people, comments, and a "via Facebook"
link at the bottom back to the original post.

You can use Facebook's Graph API Explorer to download your posts in JSON format:

https://developers.facebook.com/tools/explorer?method=GET&path=212038%3Ffields%3Did%2Cname%2Cposts.limit(9999)

This could be expanded to download posts from Facebook automatically, or even
converted to a webapp. It's also a good starting point for doing the same thing
for Twitter, Google+, and similar social networks. I don't plan to do any of
that in the near future, but I'm happy to help anyone else who wants to!

This script is in the public domain.

TODO:
- multiple picture upload. if you attach multiple pictures to an FB post,
this currently only uploads the first to WP.
"""

__author__ = 'Ryan Barrett <public@ryanb.org>'

import datetime
import itertools
import logging
import json
import os.path
import re
import sys
import time
import urllib2
import urlparse
import xmlrpclib

from activitystreams import facebook


# Publish these post types.
POST_TYPES = ('link', 'checkin', 'video')  # , 'photo', 'status', ...

# Publish these status types.
STATUS_TYPES = ('shared_story', 'added_photos', 'mobile_status_update')
  # 'wall_post', 'approved_friend', 'created_note', 'tagged_in_photo', ...

# Don't publish posts from these applications
APPLICATION_BLACKLIST = ('Likes', 'Links', 'twitterfeed')

# Attach these tags to the WordPress posts.
POST_TAGS = ['from-facebook']

# Uploaded photos are scaled to this width in pixels. They're also linked to
# the full size image.
SCALED_IMG_WIDTH = 500

# Delay between API calls to WordPress. It complains if you post too fast.
PAUSE_SEC = 1


def object_to_wordpress(xmlrpc, obj):
  """Translates an object and posts it to WordPress.

  Args:
    obj: dict, a decoded JSON ActivityStreams object
    xmlrpc: XmlRpc object
  """
  date = parse_iso8601(obj['published'])
  content = obj.get('content', '')
  location = obj.get('location')
  image = obj.get('image', {}).get('url', {})

  # extract title
  first_phrase = re.search('^[^,.:;?!]+', content)
  if first_phrase:
    title = first_phrase.group()
  elif location and 'displayName' in location:
    title = 'At ' + location['displayName']
  else:
    title = date.date().isoformat()

    # TODO
  # # check whether we should publish this
  # ptype = post.get('type')
  # stype = post.get('status_type')
  # app = post.get('application', {}).get('name')
  # if ((ptype not in POST_TYPES and stype not in STATUS_TYPES) or
  #     (app and app in APPLICATION_BLACKLIST) or
  #     # posts with 'story' aren't explicit posts. they're friend approvals or
  #     # likes or photo tags or comments on other people's posts.
  #     'story' in obj):
  #   logging.info('Skipping %s' % title)
  #   return

  # attachments, e.g. links (aka articles)
  for att in obj.get('attachments', []):
    if att.get('objectType') == 'article':
      url = att.get('url')
      name = att.get('displayName', url)
      content += """
<p><a class="fb-link" alt="%s" href="%s">
<img class="fb-link-thumbnail" src="%s" />
<span class="fb-link-name">%s</span>
""" % (name, url, image, name)
      summary = att.get('summary')
      if summary:
        content += '<span class="fb-link-summary">%s</span>\n' % summary
      content += '</p>'

  # tags (checkin, people, etc)
  content += '\n<p class="fb-tags">\n'

  # location
  if location:
    content += '<span class="fb-checkin"> at <a href="%s">%s</a></span>\n' % (
      location['url'], location['displayName'])

  # tags
  tags = obj.get('tags')
  if tags:
    content += '<span class="fb-with"> with '
    content += ', '.join('<a href="%s">%s</a>' % (t['url'], t['displayName'])
                         for t in tags)
    content += '</span>'

  content += '</p>\n'

  # photo
  # TODO
  # if (ptype == 'photo' or stype == 'added_photos') and image.endswith('_s.jpg'):
    # orig_image = image[:-6] + '_o.jpg'
  if obj.get('objectType') == 'photo' and image:
    logging.info('Downloading %s', image)
    resp = urllib2.urlopen(image)
    filename = os.path.basename(urlparse.urlparse(image).path)
    mime_type = resp.info().gettype()

    logging.info('Uploading as %s', mime_type)
    resp = xmlrpc.upload_file(filename, mime_type, resp.read())
    content += ("""
<p><a class="shutter" href="%(url)s">
  <img class="alignnone shadow" title="%(file)s" src="%(url)s" width='""" +
      str(SCALED_IMG_WIDTH) + """' />
</a></p>
""") % resp

  # "via Facebook"
  content += """<p class="fb-via">
<a href="%s">via Facebook</a>
</p>""" % obj.get('url')

  # post!
  logging.info('Publishing %s', title)
  post_id = xmlrpc.new_post({
    'post_type': 'post',
    'post_status': 'publish',
    'post_title': title,
    # supposedly if you leave this unset, it defaults to the authenticated user
    # 'post_author': 0,
    'post_content': content,
    'post_date': date,
    'comment_status': 'open',
    # WP post tags are now implemented as taxonomies:
    # http://codex.wordpress.org/XML-RPC_WordPress_API/Categories_%26_Tags
    'terms_names': {'post_tag': POST_TAGS},
    })
  # WP doesn't like it when you post too fast
  time.sleep(PAUSE_SEC)

  for reply in obj.get('replies', {}).get('items', []):
    author = reply.get('author', {})
    content = reply.get('content')
    if not content:
      continue
    logging.info('Publishing reply "%s"', content[:30])

    content += ('\n<cite><a href="%s">via Facebook</a></cite>' % reply.get('url'))

    comment_id = xmlrpc.new_comment(post_id, {
          'author': author.get('displayName', 'Anonymous'),
          'author_url': author.get('url'),
          'content': content,
          })

    published = reply.get('published')
    if published:
      date = parse_iso8601(published)
      logging.info("Updating reply's time to %s", date)
      xmlrpc.edit_comment(comment_id, {'date_created_gmt': date})

    # WP doesn't like it when you post too fast
    time.sleep(PAUSE_SEC)


def main(args):
  logging.getLogger().setLevel(logging.INFO)
  if len(args) != 4:
    print >> sys.stderr, \
        'Usage: freedom.py XMLRPC_URL USERNAME PASSWORD < FILENAME'
    return 1

  logging.info('Reading posts from stdin')
  data = sys.stdin.read()
  posts = json.loads(data)['posts']['data']

  url, user, passwd = args[1:]
  logging.info('Connecting to %s as %s', *args[1:3])
  xmlrpc = XmlRpc(url, 0, user, passwd, verbose=False)

  for post in posts:
    obj = facebook.Facebook(None).post_to_object(post)
    if obj:
      object_to_wordpress(xmlrpc, obj)

  print 'Done.'


def parse_iso8601(str):
  """Parses an ISO 8601 date/time string and returns a datetime object.
  """
  # example: 2012-07-23T05:54:49+0000
  # remove the time zone offset at the end, then parse
  return datetime.datetime.strptime(re.sub('[+-][0-9]{4}$', '', str),
                                    '%Y-%m-%dT%H:%M:%S')


class XmlRpc(object):
  """A minimal XML-RPC interface to a WordPress blog.

  Details: http://codex.wordpress.org/XML-RPC_WordPress_API

  TODO: error handling

  Class attributes:
    transport: Transport instance passed to ServerProxy()

  Attributes:
    proxy: xmlrpclib.ServerProxy
    blog_id: integer
    username: string, username for authentication, may be None
    password: string, username for authentication, may be None
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
 

if __name__ == '__main__':
  main(sys.argv)
