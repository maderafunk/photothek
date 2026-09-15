import tempfile
from pathlib import Path
import unittest
import build

class FakeClient:
    def __init__(self, pages):
        self.pages = pages
    def get(self, address):
        return self.pages[address]

class GalleryTests(unittest.TestCase):
    def test_feed_uses_publication_order_not_document_order(self):
        feed = '''<rss><channel>
          <item><title>Old</title><link>https://example.com/old</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate><description>&lt;img src="/old.jpg"&gt;</description></item>
          <item><title>New</title><link>https://example.com/new</link><pubDate>Tue, 01 Sep 2026 10:00:00 GMT</pubDate><description>&lt;img src="/new.jpg"&gt;</description></item>
        </channel></rss>'''
        site = {'url': 'https://example.com/', 'feed': 'https://example.com/feed', 'name': 'Example', 'images': 1}
        photos = build.feed_images(FakeClient({site['feed']: feed}), site)
        self.assertEqual(photos[0]['src'], 'https://example.com/new.jpg')

    def test_cdn_srcset_commas_are_preserved(self):
        node = build.Node('img', {'src': '/a.jpg', 'srcset': 'https://example.com/image/w_320,q_auto/a.jpg 320w, https://example.com/image/w_640,q_auto/a.jpg 640w, https://example.com/image/w_1200,q_auto/a.jpg 1200w'})
        self.assertEqual(build.image(node, 'https://example.com')['src'], 'https://example.com/image/w_640,q_auto/a.jpg')

    def test_lensculture_excludes_advertising(self):
        markup = '''<a href="/awards"><img class="recent-articles--cover-photo" src="/ad.jpg"></a>
        <a href="/articles/story"><img class="recent-articles--cover-photo" src="/photo.jpg" alt="Story"></a>'''
        site = {'url': 'https://example.com/', 'name': 'Example', 'mode': 'lensculture', 'images': 3}
        photos = build.discover(FakeClient({site['url']: markup}), site)
        self.assertEqual([p['src'] for p in photos], ['https://example.com/photo.jpg'])

    def test_source_text_cannot_inject_scripts(self):
        site = {'id': 'example', 'url': 'https://example.com/', 'name': '<script>alert(1)</script>', 'images': 1}
        records = {'example': {'images': [{'src': 'https://example.com/photo.jpg', 'alt': '\" onerror=\"alert(1)'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            build.render({'sites': [site]}, records, Path(tmp))
            doc = build.Document((Path(tmp) / 'index.html').read_text()).root
            self.assertEqual(list(doc.walk('script')), [])
            self.assertNotIn('onerror', next(doc.walk('img')).attrs)
        with self.assertRaises(ValueError):
            build.url('javascript:alert(1)')

if __name__ == '__main__':
    unittest.main()
