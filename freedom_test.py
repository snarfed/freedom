#!/usr/bin/python
"""Unit tests for freedom.py.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import datetime
import mox

from webutil import testutil
import freedom

BLOG_ID = 123


class Test(testutil.HandlerTest):

  xmlrpc = freedom.XmlRpc('http://abc/def.php', BLOG_ID, 'my_user', 'my_passwd')

  def setUp(self):
    super(Test, self).setUp()
    freedom.PAUSE_SEC = 0
    self.xmlrpc.proxy.wp = self.mox.CreateMockAnything()

  def assert_equals_cmp(self, expected):
    """A Mox comparator that uses HandlerTest.assert_equals."""
    def ae_cmp(actual):
      self.assert_equals(expected, actual)
      return True
    return mox.Func(ae_cmp)

  def test_basic(self):
    self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
      self.assert_equals_cmp({
        'post_type': 'post',
        'post_status': 'publish',
        'post_title': 'Anyone in or near Paris right now',
        'post_content': """\
Anyone in or near Paris right now? Interested in dinner any time Sun-Wed? There are a couple more chefs I'm hoping to check out before I head south, and I also have a seat free for an incredible reservation Tues night.
<p class="fb-tags">
</p>
<p class="fb-via">
<a href="http://facebook.com/212038/posts/157673343490">via Facebook</a>
</p>""",
        'post_date': datetime.datetime(2009, 10, 15, 22, 05, 49),
        'comment_status': 'open',
        'terms_names': {'post_tag': freedom.POST_TAGS},
        }))

    self.mox.ReplayAll()
    freedom.object_to_wordpress(self.xmlrpc, {
        'author': {
          'displayName': 'Ryan Barrett',
          'id': 'tag:facebook.com,2012:212038',
          'image': {'url': 'http://graph.facebook.com/212038/picture?type=large'},
          'url': 'http://facebook.com/212038',
          },
        'content': "Anyone in or near Paris right now? Interested in dinner any time Sun-Wed? There are a couple more chefs I'm hoping to check out before I head south, and I also have a seat free for an incredible reservation Tues night.",
        'id': 'tag:facebook.com,2012:212038_157673343490',
        'objectType': 'note',
        'published': '2009-10-15T22:05:49+0000',
        'updated': '2009-10-16T03:50:08+0000',
        'url': 'http://facebook.com/212038/posts/157673343490',
        })

  def test_comments(self):
    post_id = 222
    comment_id = 333
    self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
      self.assert_equals_cmp({
        'post_type': 'post',
        'post_status': 'publish',
        'post_title': 'New blog post',
        'post_content': """\
New blog post: World Series 2010 <a href="http://bit.ly/9HrEU5">http://bit.ly/9HrEU5</a>
<p class="fb-tags">
</p>
<p class="fb-via">
<a href="http://facebook.com/212038/posts/124561947600007">via Facebook</a>
</p>""",
        'post_date': datetime.datetime(2010, 10, 28, 00, 04, 03),
        'comment_status': 'open',
        'terms_names': {'post_tag': freedom.POST_TAGS},
        })).AndReturn(post_id)
    self.xmlrpc.proxy.wp.newComment(BLOG_ID, '', '', post_id,
      self.assert_equals_cmp({
        'author': 'Ron Ald',
        'author_url': 'http://facebook.com/513046677',
        'content': """New blog: You're awesome.
<cite><a href="http://facebook.com/212038/posts/124561947600007?comment_id=672819">via Facebook</a></cite>""",
        })).AndReturn(comment_id)
    self.xmlrpc.proxy.wp.editComment(BLOG_ID, 'my_user', 'my_passwd', comment_id, {
        'date_created_gmt': datetime.datetime(2010, 10, 28, 0, 23, 4),
        })

    self.mox.ReplayAll()
    freedom.object_to_wordpress(self.xmlrpc, {
        'author': {
          'displayName': 'Ryan Barrett',
          'id': 'tag:facebook.com,2012:212038',
          'image': {'url': 'http://graph.facebook.com/212038/picture?type=large'},
          'url': 'http://facebook.com/212038',
          },
        'content': 'New blog post: World Series 2010 <a href="http://bit.ly/9HrEU5">http://bit.ly/9HrEU5</a>',
        'id': 'tag:facebook.com,2012:212038_124561947600007',
        'objectType': 'note',
        'published': '2010-10-28T00:04:03+0000',
        'replies': {
          'items': [{
              'author': {
                'displayName': 'Ron Ald',
                'id': 'tag:facebook.com,2012:513046677',
                'image': {'url': 'http://graph.facebook.com/513046677/picture?type=large'},
                'url': 'http://facebook.com/513046677',
                },
              'content': "New blog: You're awesome.",
              'id': 'tag:facebook.com,2012:212038_124561947600007_672819',
              'inReplyTo': {'id': 'tag:facebook.com,2012:212038_124561947600007'},
              'objectType': 'comment',
              'published': '2010-10-28T00:23:04+0000',
              'url': 'http://facebook.com/212038/posts/124561947600007?comment_id=672819',
              }],
          'totalItems': 1,
          },
        'updated': '2010-10-28T00:23:04+0000',
        'url': 'http://facebook.com/212038/posts/124561947600007',
        })

  def test_link(self):
    self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
      self.assert_equals_cmp({
        'post_type': 'post',
        'post_status': 'publish',
        'post_title': 'Paul Graham inspired me to put this at the top of my todo list',
        'post_content': """\
Paul Graham inspired me to put this at the top of my todo list, to force myself to think about it regularly.
<p><a class="fb-link" alt="The Top of My Todo List" href="http://paulgraham.com/todo.html">
<img class="fb-link-thumbnail" src="http://my/image.jpg" />
<span class="fb-link-name">The Top of My Todo List</span>
<span class="fb-link-summary">paulgraham.com</span>
</p>
<p class="fb-tags">
</p>
<p class="fb-via">
<a href="http://facebook.com/212038/posts/407323642625868">via Facebook</a>
</p>""",
        'post_date': datetime.datetime(2012, 4, 22, 17, 8, 4),
        'comment_status': 'open',
        'terms_names': {'post_tag': freedom.POST_TAGS},
        }))

    self.mox.ReplayAll()
    freedom.object_to_wordpress(self.xmlrpc, {
        'attachments': [{
            'displayName': 'The Top of My Todo List',
            'objectType': 'article',
            'summary': 'paulgraham.com',
            'url': 'http://paulgraham.com/todo.html',
            }],
        'author': {
          'displayName': 'Ryan Barrett',
          'id': 'tag:facebook.com,2012:212038',
          'image': {'url': 'http://graph.facebook.com/212038/picture?type=large'},
          'url': 'http://facebook.com/212038',
          },
        'content': 'Paul Graham inspired me to put this at the top of my todo list, to force myself to think about it regularly.',
        'id': 'tag:facebook.com,2012:212038_407323642625868',
        'image': {'url': 'http://my/image.jpg'},
        'objectType': 'article',
        'published': '2012-04-22T17:08:04+0000',
        'updated': '2012-04-22T17:08:04+0000',
        'url': 'http://facebook.com/212038/posts/407323642625868',
        })

  def test_location(self):
    self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
      self.assert_equals_cmp({
        'post_type': 'post',
        'post_status': 'publish',
        'post_title': 'Clothes shopping',
        'post_content': """\
Clothes shopping. Grudgingly.
<p><a class="fb-link" alt="name: Macys San Francisco Union Square" href="https://www.facebook.com/MacysSanFranciscoUnionSquareCA">
<img class="fb-link-thumbnail" src="https://macys/picture.jpg" />
<span class="fb-link-name">name: Macys San Francisco Union Square</span>
<span class="fb-link-summary">Ryan checked in at Macys San Francisco Union Square.</span>
</p>
<p class="fb-tags">
<span class="fb-checkin"> at <a href="http://facebook.com/161569013868015">place: Macys San Francisco Union Square</a></span>
</p>
<p class="fb-via">
<a href="http://facebook.com/212038/posts/10100397129690713">via Facebook</a>
</p>""",
        'post_date': datetime.datetime(2012, 10, 14, 19, 41, 30),
        'comment_status': 'open',
        'terms_names': {'post_tag': freedom.POST_TAGS},
        }))

    self.mox.ReplayAll()
    freedom.object_to_wordpress(self.xmlrpc, {
        'attachments': [{
            'content': 'We thank you for your enthusiasm for Macys!',
            'displayName': 'name: Macys San Francisco Union Square',
            'objectType': 'article',
            'summary': 'Ryan checked in at Macys San Francisco Union Square.',
            'url': 'https://www.facebook.com/MacysSanFranciscoUnionSquareCA',
            }],
        'author': {
          'displayName': 'Ryan Barrett',
          'id': 'tag:facebook.com,2012:212038',
          'image': {'url': 'http://graph.facebook.com/212038/picture?type=large'},
          'url': 'http://facebook.com/212038',
          },
        'content': 'Clothes shopping. Grudgingly.',
        'id': 'tag:facebook.com,2012:212038_10100397129690713',
        'image': {'url': 'https://macys/picture.jpg'},
        'location': {
          'displayName': 'place: Macys San Francisco Union Square',
          'id': '161569013868015',
          'latitude': 37.787235321839,
          'longitude': -122.40721521845,
          'position': '+37.787235-122.407215/',
          'url': 'http://facebook.com/161569013868015',
          },
        'objectType': 'note',
        'published': '2012-10-14T19:41:30+0000',
        'updated': '2012-10-15T03:59:48+0000',
        'url': 'http://facebook.com/212038/posts/10100397129690713',
        })

  def test_linkify_content(self):
    self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
      self.assert_equals_cmp({
        'post_type': 'post',
        'post_status': 'publish',
        'post_title': 'Oz Noy trio is killing it',
        'post_content': """\
Oz Noy trio is killing it.
<a href="http://oznoy.com/">http://oznoy.com/</a>
<p><a class="fb-link" alt="The 55 Bar" href="https://www.facebook.com/pages/The-55-Bar/136676259709087">
<img class="fb-link-thumbnail" src="https://fbcdn-profile-a.akamaihd.net/abc.png" />
<span class="fb-link-name">The 55 Bar</span>
<span class="fb-link-summary">Ryan checked in at The 55 Bar.</span>
</p>
<p class="fb-tags">
</p>
<p class="fb-via">
<a href="http://facebook.com/212038/posts/10100242451207633">via Facebook</a>
</p>""",
        'post_date': datetime.datetime(2012, 4, 26, 4, 29, 56),
        'comment_status': 'open',
        'terms_names': {'post_tag': freedom.POST_TAGS},
        }))

    self.mox.ReplayAll()
    freedom.object_to_wordpress(self.xmlrpc, {
        'attachments': [{
            'displayName': 'The 55 Bar',
            'objectType': 'article',
            'summary': 'Ryan checked in at The 55 Bar.',
            'url': 'https://www.facebook.com/pages/The-55-Bar/136676259709087',
            }],
        'author': {
          'displayName': 'Ryan Barrett',
          'id': 'tag:facebook.com,2012:212038',
          'image': {'url': 'http://graph.facebook.com/212038/picture?type=large'},
          'url': 'http://facebook.com/212038',
          },
        'content': 'Oz Noy trio is killing it.\n<a href="http://oznoy.com/">http://oznoy.com/</a>',
        'id': 'tag:facebook.com,2012:212038_10100242451207633',
        'image': {'url': 'https://fbcdn-profile-a.akamaihd.net/abc.png'},
        'objectType': 'note',
        'published': '2012-04-26T04:29:56+0000',
        'updated': '2012-04-26T04:29:56+0000',
        'url': 'http://facebook.com/212038/posts/10100242451207633',
        })

  def test_mention_and_with_tags(self):
    self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
      self.assert_equals_cmp({
        'post_type': 'post',
        'post_status': 'publish',
        'post_title': "discovered in the far back of a dusty cabinet at my parents' house",
        'post_content': """\
discovered in the far back of a dusty cabinet at my parents' house. been sitting there for over five years. evidently the camus 140th anniversary is somewhat special, and damn good.

cc <a class="fb-mention" href="http://facebook.com/profile.php?id=13307262">Daniel Meredith</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=9374038">Warren Ahner</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=201963">Steve Garrity</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=1506309346">Devon LaHar</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=100000224384191">Gina Rossman</a>
<p><a class="fb-link" alt="https://www.facebook.com/photo.php?fbid=998665748673&set=a.995695740593.2393090.212038&type=1&relevant_count=1" href="https://www.facebook.com/photo.php?fbid=998665748673&set=a.995695740593.2393090.212038&type=1&relevant_count=1">
<img class="fb-link-thumbnail" src="{}" />
<span class="fb-link-name">https://www.facebook.com/photo.php?fbid=998665748673&set=a.995695740593.2393090.212038&type=1&relevant_count=1</span>
</p>
<p class="fb-tags">
<span class="fb-with"> with <a href="http://facebook.com/100000224384191">Gina Rossman</a>, <a href="http://facebook.com/1506309346">Devon LaHar</a>, <a href="http://facebook.com/201963">Steve Garrity</a>, <a href="http://facebook.com/9374038">Warren Ahner</a></span></p>
<p class="fb-via">
<a href="http://facebook.com/212038/posts/998665783603">via Facebook</a>
</p>""",
        'post_date': datetime.datetime(2011, 12, 28, 3, 36, 46),
        'comment_status': 'open',
        'terms_names': {'post_tag': freedom.POST_TAGS},
        }))

    self.mox.ReplayAll()
    freedom.object_to_wordpress(self.xmlrpc, {
        'attachments': [{
            'objectType': 'article',
            'url': 'https://www.facebook.com/photo.php?fbid=998665748673&set=a.995695740593.2393090.212038&type=1&relevant_count=1',
            }],
        'author': {
          'displayName': 'Ryan Barrett',
          'id': 'tag:facebook.com,2012:212038',
          'image': {'url': 'http://graph.facebook.com/212038/picture?type=large'},
          'url': 'http://facebook.com/212038',
          },
        'content': 'discovered in the far back of a dusty cabinet at my parents\' house. been sitting there for over five years. evidently the camus 140th anniversary is somewhat special, and damn good.\n\ncc <a class="fb-mention" href="http://facebook.com/profile.php?id=13307262">Daniel Meredith</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=9374038">Warren Ahner</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=201963">Steve Garrity</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=1506309346">Devon LaHar</a>, <a class="fb-mention" href="http://facebook.com/profile.php?id=100000224384191">Gina Rossman</a>',
        'id': 'tag:facebook.com,2012:212038_998665783603',
        'objectType': 'note',
        'published': '2011-12-28T03:36:46+0000',
        'tags': [{
            'displayName': 'Gina Rossman',
            'id': 'tag:facebook.com,2012:100000224384191',
            'objectType': 'person',
            'url': 'http://facebook.com/100000224384191',
            }, {
            'displayName': 'Devon LaHar',
            'id': 'tag:facebook.com,2012:1506309346',
            'objectType': 'person',
            'url': 'http://facebook.com/1506309346',
            }, {
            'displayName': 'Steve Garrity',
            'id': 'tag:facebook.com,2012:201963',
            'objectType': 'person',
            'url': 'http://facebook.com/201963',
            }, {
            'displayName': 'Warren Ahner',
            'id': 'tag:facebook.com,2012:9374038',
            'objectType': 'person',
            'url': 'http://facebook.com/9374038',
            }],
        'updated': '2011-12-28T03:36:46+0000',
        'url': 'http://facebook.com/212038/posts/998665783603'})


#   def test_picture(self):
#     self.xmlrpc.proxy.wp.uploadFile(BLOG_ID, 'my_user', 'my_passwd',
#                                  ...)
#     self.xmlrpc.proxy.wp.newPost(BLOG_ID, 'my_user', 'my_passwd',
#       self.assert_equals_cmp({
#         'post_type': 'post',
#         'post_status': 'publish',
#         'post_title': 'Clothes shopping',
#         'post_content': '''\
# Clothes shopping. Grudgingly.
# <p><a class='fb-link' alt='We thank you for your enthusiasm for Macys!' href='https://www.facebook.com/MacysSanFranciscoUnionSquareCA'>
# <img class='fb-link-thumbnail' src='https://macys/picture.jpg' />
# <span class='fb-link-name'>https://www.facebook.com/MacysSanFranciscoUnionSquareCA</span>
# <span class='fb-link-summary'>Ryan checked in at Macys San Francisco Union Square.</span>
# </p>
# <p class='fb-tags'>
# <span class='fb-checkin'> at <a href='http://facebook.com/161569013868015'>Macys San Francisco Union Square</a></span>
# </p>
# <p class='fb-via'>
# <a href='http://facebook.com/212038/posts/10100419011125143'>via Facebook</a>
# </p>''',
#         'post_date': datetime.datetime(2012, 10, 14, 19, 41, 30),
#         'comment_status': 'open',
#         'terms_names': {'post_tag': freedom.POST_TAGS},
#         }))

#     self.mox.ReplayAll()
#     freedom.object_to_wordpress(self.xmlrpc,{
#       'id': '212038_10100419011125143',
#       'from': {
#         'name': 'Ryan Barrett',
#         'id': '212038'
#       },
#       'story': 'Ryan Barrett added a new photo.',
#       'picture': 'https://my/photo.jpg',
#       'link': 'https://www.facebook.com/photo.php?fbid=10100419011060273&set=a.10100419008909583.2449717.212038&type=1&relevant_count=1',
#       'icon': 'https://s-static.ak.facebook.com/rsrc.php/v2/yz/r/StEh3RhPvjk.gif',
#       'type': 'photo',
#       'status_type': 'added_photos',
#       'object_id': '10100419011060273',
#       'created_time': '2012-11-06T05:50:21+0000',
#       'updated_time': '2012-11-07T03:39:11+0000'
#     })
