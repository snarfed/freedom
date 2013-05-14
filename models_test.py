"""Unit tests for models.py.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import json
import mox

import appengine_config
from models import Migratable
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


class SourceTest(testutil.HandlerTest):

  def _test_create_new(self):
    FakeSource.create_new(self.handler)
    self.assertEqual(1, FakeSource.all().count())

    tasks = self.taskqueue_stub.GetTasks('poll')
    self.assertEqual(1, len(tasks))
    source = FakeSource.all().get()
    self.assertEqual('/_ah/queue/poll', tasks[0]['url'])
    params = testutil.get_task_params(tasks[0])
    self.assertEqual(str(source.key()), params['source_key'])
    self.assertEqual('1970-01-01-00-00-00',
                     params['last_polled'])

  def test_create_new(self):
    self.assertEqual(0, FakeSource.all().count())
    self._test_create_new()

  def test_create_new_already_exists(self):
    FakeSource.new(self.handler).save()
    FakeSource.key_name_counter -= 1
    self._test_create_new()


class MigratableTest(testutil.HandlerTest):

  def setUp(self):
    super(MigratableTest, self).setUp()
    self.post = Migratable(key_name='tag:xyz', vars=json.dumps(POST_VARS))
    appengine_config.USER_KEY_HANDLER_SECRET = 'my_secret'

  def test_get_or_save(self):
    self.assertEqual(0, Migratable.all().count())
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


class CommentTest(testutil.ModelsTest):

  def test_get_or_save(self):
    self.sources[0].save()

    comment = self.comments[0]
    self.assertEqual(0, Comment.all().count())
    self.assertEqual(0, len(self.taskqueue_stub.GetTasks('propagate')))

    # new. should add a propagate task.
    saved = comment.get_or_save()
    self.assertEqual(1, Comment.all().count())
    self.assertTrue(saved.is_saved())
    self.assertEqual(comment.key(), saved.key())
    self.assertEqual(comment.source, saved.source)
    self.assertEqual(comment.dest, saved.dest)

    tasks = self.taskqueue_stub.GetTasks('propagate')
    self.assertEqual(1, len(tasks))
    self.assertEqual(str(comment.key()),
                     testutil.get_task_params(tasks[0])['comment_key'])
    self.assertEqual('/_ah/queue/propagate', tasks[0]['url'])

    # existing. no new task.
    same = saved.get_or_save()
    self.assertEqual(saved.source.key(), same.source.key())
    self.assertEqual(saved.dest.key(), same.dest.key())
    self.assertEqual(1, len(tasks))

    # # different source and dest
    # # i don't do this assert any more, but i might come back to it later.
    # diff = Comment(key_name=comment.key().name(),
    #                source=self.sources[0], dest=self.dests[1])
    # self.assertRaises(AssertionError, diff.get_or_save)
    # diff = Comment(key_name=comment.key().name(),
    #                source=self.sources[1], dest=self.dests[0])
    # self.assertRaises(AssertionError, diff.get_or_save)
