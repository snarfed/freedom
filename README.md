freedom.io
==========

Sets free your Facebook, Twitter, and Google+ posts by copying them to your WordPress blog via XML-RPC, with all formatting and details intact.

Social networks keep your memories locked up. Take them back and set them free! Copy your posts, pictures, and other content to a blog of your choice.

License: This project is placed in the public domain.


Development
===========

Requirements:

- Python 2.7
- Google App Engine (either dev_appserver or prod), which includes:
  - django
  - mox (for tests)
  - webob
  - yaml
- Libraries in git submodules (be sure to run git submodule init!):
  - http://github.com/snarfed/activitystreams-unofficial
  - http://github.com/musicmetric/google-api-python-client
  - http://github.com/adamjmcgrath/httplib2
  - http://github.com/wishabi/python-gflags
  - http://github.com/michaelhelmick/python-tumblpy
  - http://github.com/kennethreitz/requests
  - http://github.com/requests/requests-oauthlib
  - http://github.com/idan/oauthlib
  - http://github.com/snarfed/gdata-python-client


TODO
====
- SSL? the cert itself costs ~$60/yr in general:
  http://www.pair.com/services/e-commerce/pairssl/
  https://www.volcanicpixels.com/ssl/
  ...or free from StartSSL!
  https://konklone.com/post/switch-to-https-now-for-free
  ...and SSL on app engine costs $9/mo for SNI or $39/mo for a VIP (supports old
  IE, Win XP, pre-Honeycomb Android):
  https://developers.google.com/appengine/docs/pricing#cost_resource
  https://developers.google.com/appengine/docs/ssl
- test: use mockfb to run a migration to local snarfed
- migration page
  advice from @colbyh: use Bootstrap or Yui or maybe Zurb for UI, d3 for charts
  and visualizations, JQuery and maybe Underscore for JS. simple XHR polling is
  fine. set cookie to remember users with existing migration(s), on front page
  show summary and link for each migration in place of splash image, leave rest
  of new migration form intact.
- cancel migration
- posthaven
- finish post/comment processing for:
  - facebook
  - twitter
  - g+
- switch to using paging and API requests in activitystreams-unofficial
- make tasks transactional where necessary
- port to ndb?
- migration options
  - twitter: exclude @ replies
- once it's done, maybe enter it in http://www.google.com/events/gcdc2013/ ?
