# Publish these post types.
POST_TYPES = ('link', 'checkin', 'video')  # , 'photo', 'status', ...

# Publish these status types.
STATUS_TYPES = ('shared_story', 'added_photos', 'mobile_status_update')
  # 'wall_post', 'approved_friend', 'created_note', 'tagged_in_photo', ...

# Don't publish posts from these applications
APPLICATION_BLACKLIST = ('Likes', 'Links', 'twitterfeed')

# Attach these tags to the WordPress posts.
POST_TAGS = ['freedom.io']

# Uploaded photos are scaled to this width in pixels. They're also linked to
# the full size image.
SCALED_IMG_WIDTH = 500


# TODO: test
def preprocess_facebook(post):
  """Tweaks a Facebook post before converting to ActivityStreams

  Args:
    post: dict, decoded JSON Facebook post. (This dict will be modified!)

  Returns: the processed post dict, or None if it should not be posted
  """
  app = post.get('application', {}).get('name')
  if ((post.get('type') not in POST_TYPES and
       post.get('status_type') not in STATUS_TYPES) or
      (app and app in APPLICATION_BLACKLIST) or
      # posts with 'story' aren't explicit posts. they're friend approvals or
      # likes or photo tags or comments on other people's posts.
      'story' in obj):
    logging.info('Skipping %s', post.get('id'))
    return None

  # for photos, get a larger version
  image = post.get('image', '')
  if (ptype == 'photo' or stype == 'added_photos') and image.endswith('_s.jpg'):
    post['image'] = image[:-6] + '_o.jpg'

  return post


def preprocess_twitter(post):
  """Tweaks a Twitter tweet before converting to ActivityStreams

  Args:
    post: dict, decoded JSON tweet. (This dict will be modified!)

  Returns: the processed post dict, or None if it should not be posted
  """
  # TODO
  return post


def preprocess_googleplus(activity):
  """Tweaks a Google+ activity before rendering and posting.

  Args:
    activity: dict, decoded JSON Google+ activity.

  Returns: the processed activity dict, or None if it should not be posted
  """
  # TODO: use get_salmon from ~/src/salmon-unofficial/googleplus.py
  return post


