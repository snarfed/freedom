#!/usr/bin/python
"""Unit tests for tasks.py.
"""

__author__ = ['Ryan Barrett <salmon@ryanb.org>']

import datetime
import json
import mox
import urlparse
from webob import exc

from fakes import FakeSource
from models import Source
from salmon import Salmon
import tasks
from tasks import Poll, Propagate
from webutil import testutil
from webutil import webapp2

from google.appengine.ext import db


class TaskQueueTest(testutil.HandlerTest):
  """Base class for testing task handlers.

  Attributes:
    task_params: the query parameters passed in the task POST request
    post_url: the URL for post_task() to post to
    now: the datetime to be returned by datetime.now()
    salmon: a test data Salmon
  """
  task_params = None
  post_url = None
  now = datetime.datetime.now()

  def setUp(self):
    super(TaskQueueTest, self).setUp()
    tasks.NOW_FN = lambda: self.now
    self.salmon_vars = {'id': 'tag:foo'}
    self.salmon = Salmon(key_name='tag:foo', vars=json.dumps(self.salmon_vars))

  def post_task(self, expected_status=200):
    """Runs post() and checks the response status code."""
    resp = tasks.application.get_response(self.post_url, method='POST',
                                          POST=self.task_params)
    self.assertEqual(expected_status, resp.status_int, resp.body)


class PollTest(TaskQueueTest):

  post_url = '/_ah/queue/poll'

  def setUp(self):
    super(PollTest, self).setUp()
    self.source = FakeSource.new(self.handler)
    self.source.save()
    self.source.set_salmon([self.salmon_vars])
    self.task_params = {'source_key': self.source.key(),
                        'last_polled': '1970-01-01-00-00-00'}

  def test_poll(self):
    """A normal poll task."""
    self.assertFalse(db.get(self.salmon.key()))
    self.assertEqual([], self.taskqueue_stub.GetTasks('poll'))

    self.post_task()
    self.assertTrue(db.get(self.salmon.key()))

    source = db.get(self.source.key())
    self.assertEqual(self.now, source.last_polled)

    tasks = self.taskqueue_stub.GetTasks('poll')
    self.assertEqual(1, len(tasks))
    self.assertEqual('/_ah/queue/poll', tasks[0]['url'])

    params = testutil.get_task_params(tasks[0])
    self.assertEqual(str(source.key()),
                     params['source_key'])
    self.assertEqual(self.now.strftime(Source.POLL_TASK_DATETIME_FORMAT),
                     params['last_polled'])

  def test_existing_salmon(self):
    """Poll should be idempotent and not touch existing salmon entities.
    """
    self.salmon.status = 'complete'
    self.salmon.save()

    self.post_task()
    self.assertTrue(db.get(self.salmon.key()))
    self.assertEqual('complete', db.get(self.salmon.key()).status)

  def test_wrong_last_polled(self):
    """If the source doesn't have our last polled value, we should quit.
    """
    self.source.last_polled = datetime.datetime.utcfromtimestamp(3)
    self.source.save()
    self.post_task()
    self.assert_(db.get(self.salmon.key()) is None)

  def test_no_source(self):
    """If the source doesn't exist, do nothing and let the task die.
    """
    self.source.delete()
    self.post_task()
    self.assertEqual([], self.taskqueue_stub.GetTasks('poll'))


class PropagateTest(TaskQueueTest):

  post_url = '/_ah/queue/propagate'

  def setUp(self):
    super(PropagateTest, self).setUp()
    self.salmon.save()
    self.task_params = {'salmon_key': self.salmon.key()}

    # can't use StubOutWithMock because i want to check the value of self, not
    # args, which evidently mox can't do. :/ i get this error when I try to do
    # it with self.mox.StubOutWithMock(Salmon, 'send_slap'):
    #
    #   UnexpectedMethodCallError: Unexpected method call: __call__() -> None.
    #     Expecting: __call__(<function <lambda> at 0x363b758>) -> None
    self.slaps = []
    self.orig_send_slap = Salmon.send_slap
    Salmon.send_slap = lambda salmon: self.slaps.append(salmon)

  def tearDown(self):
    Salmon.send_slap = self.orig_send_slap
    super(PropagateTest, self).tearDown()

  def assert_salmon_is(self, status, leased_until=False):
    """Asserts that self.salmon has the given values in the datastore.
    """
    salmon = db.get(self.salmon.key())
    self.assertEqual(status, salmon.status)
    if leased_until is not False:
      self.assertEqual(leased_until, salmon.leased_until)

  def test_propagate(self):
    """A normal propagate task."""
    self.assertEqual('new', self.salmon.status)
    self.post_task()
    self.assert_salmon_is('complete', self.now + Propagate.LEASE_LENGTH)
    self.assert_entities_equal(self.slaps, self.salmon, keys_only=True)

  def test_already_complete(self):
    """If the salmon has already been propagated, do nothing."""
    self.salmon.status = 'complete'
    self.salmon.save()
    self.post_task()
    self.assert_salmon_is('complete')
    self.assertEqual([], self.slaps)

  def test_leased(self):
    """If the salmon is processing and the lease hasn't expired, do nothing."""
    self.salmon.status = 'processing'
    leased_until = self.now + datetime.timedelta(minutes=1)
    self.salmon.leased_until = leased_until
    self.salmon.save()

    self.post_task(expected_status=exc.HTTPConflict.code)
    self.assert_salmon_is('processing', leased_until)
    self.assertEqual([], self.slaps)

  def test_lease_expired(self):
    """If the salmon is processing but the lease has expired, process it."""
    self.salmon.status = 'processing'
    self.salmon.leased_until = self.now - datetime.timedelta(minutes=1)
    self.salmon.save()

    self.post_task()
    self.assert_salmon_is('complete', self.now + Propagate.LEASE_LENGTH)
    self.assert_entities_equal(self.slaps, self.salmon, keys_only=True)

  def test_no_salmon(self):
    """If the salmon doesn't exist, the request should fail."""
    self.salmon.delete()
    self.post_task(expected_status=exc.HTTPExpectationFailed.code)
    self.assertEqual([], self.slaps)

  def test_exceptions(self):
    """If any part raises an exception, the lease should be released."""
    methods = [(Propagate, 'lease_salmon'),
               (Propagate, 'complete_salmon'),
               (Salmon, 'send_slap'),
               ]

    for cls, method in methods:
      self.mox.UnsetStubs()
      self.mox.StubOutWithMock(cls, method)
      getattr(cls, method)().AndRaise(Exception('foo'))
      self.mox.ReplayAll()

      self.post_task(expected_status=500)
      self.assert_salmon_is('new', None)
      self.mox.VerifyAll()
