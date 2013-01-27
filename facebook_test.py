#!/usr/bin/python
"""Unit tests for facebook.py.
"""

__author__ = ['Ryan Barrett <freedom@ryanb.org>']

import json

import mox
import urlparse

import appengine_config
import facebook
from webutil import testutil
from webutil import webapp2

# test data
LINK_AND_COMMENTS_JSON = {
  'id': '309194992492775',
  'from': {
    'name': 'Ryan Barrett',
    'id': '212038',
    },
  'message': 'Hey managers, remember back before you were a manager?',
  'link': 'http://snarfed.org/2012-05-13_how_can_we_motivate_managers',
  'name': 'How can we motivate managers?',
  'created_time': '2012-05-14T05:40:23+0000',
  'comments': {'data': [
      {
        'id': '309194992492775_1975744',
        'from': {
          'name': 'Alice',
          'id': '123',
          },
        'message': 'foo',
        'created_time': '2012-05-14T05:54:48+0000',
        },
      {
        'id': '309194992492775_1975797',
        'from': {
          'name': 'Bob',
          'id': '456',
          },
        'message': 'bar',
        'created_time': '2012-05-14T06:15:51+0000',
        },
      ]},
  }

LINK_AND_COMMENTS_SALMON_VARS = [
  {
    'id': 'tag:facebook.com,2012:309194992492775',
    'author_name': 'Ryan Barrett',
    'author_uri': 'acct:212038@facebook-webfinger.appspot.com',
    'in_reply_to': 'http://snarfed.org/2012-05-13_how_can_we_motivate_managers',
    'content': 'Hey managers, remember back before you were a manager?',
    'title': 'Hey managers, remember back before you were a manager?',
    'updated': '2012-05-14T05:40:23+0000',
    },
  {
    'id': 'tag:facebook.com,2012:309194992492775_1975744',
    'author_name': 'Alice',
    'author_uri': 'acct:123@facebook-webfinger.appspot.com',
    'in_reply_to': 'http://snarfed.org/2012-05-13_how_can_we_motivate_managers',
    'content': 'foo',
    'title': 'foo',
    'updated': '2012-05-14T05:54:48+0000',
    },
  {
    'id': 'tag:facebook.com,2012:309194992492775_1975797',
    'author_name': 'Bob',
    'author_uri': 'acct:456@facebook-webfinger.appspot.com',
    'in_reply_to': 'http://snarfed.org/2012-05-13_how_can_we_motivate_managers',
    'content': 'bar',
    'title': 'bar',
    'updated': '2012-05-14T06:15:51+0000',
    },
  ]


class FacebookTest(testutil.HandlerTest):

  def setUp(self):
    super(FacebookTest, self).setUp()
    self.facebook = facebook.Facebook(key_name='x')
    appengine_config.FACEBOOK_APP_ID = 'my_app_id'
    appengine_config.FACEBOOK_APP_SECRET = 'my_secret'

  def test_post_to_salmon_vars(self):
    self.assert_equals({
      'id': 'tag:facebook.com,2012:10102828452385634_39170557',
      'author_name': 'Ryan Barrett',
      'author_uri': 'acct:212038@facebook-webfinger.appspot.com',
      'in_reply_to': 'http://moire/patterns',
      'content': 'moire patterns: the new look for spring.',
      'title': 'moire patterns: the new look for spring.',
      'updated': '2012-05-21T02:25:25+0000',
      },
      self.facebook.post_to_salmon_vars({
          'id': '10102828452385634_39170557',
          'from': {
            'name': 'Ryan Barrett',
            'id': '212038'
            },
          'message': 'moire patterns: the new look for spring.',
          'link': 'http://moire/patterns',
          'created_time': '2012-05-21T02:25:25+0000',
          }))

  def test_post_to_salmon_vars_minimal(self):
    salmon = self.facebook.post_to_salmon_vars({'id': '123_456'})
    self.assert_equals('tag:facebook.com,2012:123_456', salmon['id'])

  def test_post_and_comments_to_salmon_vars(self):
    self.assert_equals(
      LINK_AND_COMMENTS_SALMON_VARS,
      self.facebook.post_and_comments_to_salmon_vars(LINK_AND_COMMENTS_JSON))

  def test_get_salmon(self):
    self.facebook.access_token = 'my_token'
    self.expect_urlfetch(
      'https://graph.facebook.com/x/links?access_token=my_token',
      json.dumps({'data': [LINK_AND_COMMENTS_JSON, LINK_AND_COMMENTS_JSON]}))
    self.mox.ReplayAll()

    self.assert_equals(LINK_AND_COMMENTS_SALMON_VARS * 2,
                       self.facebook.get_salmon())

  def test_new(self):
    self.expect_urlfetch('https://graph.facebook.com/me?access_token=my_token',
                         json.dumps({'id': '1', 'name': 'Mr. Foo'}))
    self.mox.ReplayAll()

    self.handler.request = webapp2.Request.blank('?access_token=my_token')
    fb = facebook.Facebook.new(self.handler)

    self.assertEqual('1', fb.key().name())
    self.assertEqual('Mr. Foo', fb.name)
    self.assertEqual('https://graph.facebook.com/1/picture?type=small', fb.picture)
    self.assertEqual('http://facebook.com/1', fb.url)
    self.assertEqual('my_token', fb.access_token)
    self.assertEqual(self.current_user_id, fb.owner.key().name())

  def test_get_access_token(self):
    resp = facebook.application.get_response('/facebook/add', method='POST',
                                             environ={'HTTP_HOST': 'HOST'})
    self.assertEqual(302, resp.status_int)
    redirect = resp.headers['Location']

    parsed = urlparse.urlparse(redirect)
    self.assertEqual('/dialog/oauth/', parsed.path)

    expected_params = {
      'scope': ['read_stream,offline_access'],
      'client_id': ['my_app_id'],
      'redirect_uri': ['http://HOST/facebook/got_auth_code'],
      'response_type': ['code'],
      'state': ['http://HOST/facebook/got_access_token'],
      }
    self.assert_equals(expected_params, urlparse.parse_qs(parsed.query))

  def test_got_auth_code(self):
    comparator = mox.Regex('.*/oauth/access_token\?.*&code=my_auth_code.*')
    self.expect_urlfetch(comparator, 'foo=bar&access_token=my_access_token')

    self.mox.ReplayAll()
    resp = facebook.application.get_response(
      '/facebook/got_auth_code?code=my_auth_code&state=http://my/redirect_to',
      environ={'HTTP_HOST': 'HOST'})
    self.assertEqual(302, resp.status_int)
    self.assertEqual('http://my/redirect_to?access_token=my_access_token',
                     resp.headers['Location'])
