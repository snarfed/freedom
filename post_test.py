#!/usr/bin/python
"""Unit tests for post.py.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import json
import mox

import appengine_config
from post import Post
from webutil import testutil


POST_VARS = {
  'id': 'tag:facebook.com,2012:10102828452385634_39170557',
  'author_name': 'Ryan Barrett',
  'author_uri': 'acct:212038@facebook-webfinger.appspot.com',
  # TODO: this should be the original domain link
  'in_reply_to': 'tag:facebook.com,2012:10102828452385634',
  'content': 'moire patterns: the new look for spring.',
  'title': 'moire patterns: the new look for spring.',
  'updated': '2012-05-21T02:25:25+0000',
  }


class PostTest(testutil.HandlerTest):

  def setUp(self):
    super(PostTest, self).setUp()
    self.post = Post(key_name='tag:xyz', vars=json.dumps(POST_VARS))
    appengine_config.USER_KEY_HANDLER_SECRET = 'my_secret'

  def test_get_or_save(self):
    self.assertEqual(0, Post.all().count())
    self.assertEqual(0, len(self.taskqueue_stub.GetTasks('propagate')))

    # new. should add a propagate task.
    saved = self.post.get_or_save()
    self.assertTrue(saved.is_saved())
    self.assertEqual(self.post.key(), saved.key())

    tasks = self.taskqueue_stub.GetTasks('propagate')
    self.assertEqual(1, len(tasks))
    self.assertEqual(str(self.post.key()),
                     testutil.get_task_params(tasks[0])['post_key'])
    self.assertEqual('/_ah/queue/propagate', tasks[0]['url'])

    # existing. no new task.
    same = saved.get_or_save()
    self.assertEqual(1, len(tasks))

  def test_envelope(self):
    self.expect_urlfetch('https://facebook-webfinger.appspot.com/user_key'
                         '?uri=acct:ryan@facebook.com&secret=my_secret',
                         json.dumps(USER_KEY_JSON))
    self.mox.ReplayAll()

    envelope = self.post.envelope('acct:ryan@facebook.com')\
        .replace('>', '>\n').replace('</', '\n</')
    self.assert_multiline_equals(ENVELOPE_XML, envelope)

  def test_send_slap(self):
    pass
