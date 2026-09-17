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
    def test_minimal_config_generates_id_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sites.yml'
            path.write_text('sites:\n  - name: Example Magazine\n    link: https://example.com/\n')
            site = build.config(path)['sites'][0]
        self.assertEqual(site['id'], 'example-magazine')
        self.assertEqual(site['url'], 'https://example.com/')
        self.assertEqual(site['images'], 3)

    def test_legacy_site_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sites.yml'
            path.write_text('sites:\n  - name: Example\n    url: https://example.com/\n    mode: feed\n')
            with self.assertRaisesRegex(ValueError, 'use only name and link'):
                build.config(path)

    def test_auto_continues_after_broken_feed(self):
        homepage = '''<link rel="alternate" type="application/rss+xml" href="/bad-feed">
        <a href="/stories/one"><img data-src="/photo.jpg" alt="Story"></a>'''
        site = {'url': 'https://example.com/', 'name': 'Example', 'images': 1}
        photos = build.discover(FakeClient({
            site['url']: homepage,
            'https://example.com/bad-feed': '<not xml',
            'https://example.com/feed': '<not xml',
            'https://example.com/feed.xml': '<not xml',
            'https://example.com/rss.xml': '<not xml',
        }), site)
        self.assertEqual(photos[0]['src'], 'https://example.com/photo.jpg')
        self.assertEqual(photos[0]['article'], 'https://example.com/stories/one')

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

    def test_render_includes_date_without_time(self):
        site = {'id': 'example', 'url': 'https://example.com/', 'name': 'Example', 'images': 3}
        with tempfile.TemporaryDirectory() as tmp:
            build.render({'sites': [site]}, {}, Path(tmp), updated_on='2026-09-17')
            html = (Path(tmp) / 'index.html').read_text()
        self.assertIn('<time datetime="2026-09-17">2026-09-17</time>', html)
        self.assertNotIn('2026-09-17T', html)

    def test_tile_uses_css_only_flip_control(self):
        site = {'id': 'example', 'url': 'https://example.com/', 'name': 'Example', 'images': 3}
        records = {'example': {'images': [{'src': 'https://example.com/photo.jpg'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            build.render({'sites': [site]}, records, Path(tmp), updated_on='2026-09-17')
            doc = build.Document((Path(tmp) / 'index.html').read_text()).root
        toggle = next(doc.walk('input'))
        self.assertEqual(toggle.attrs['type'], 'checkbox')
        self.assertEqual(toggle.attrs['id'], 'tile-example')
        self.assertEqual(next(doc.walk('label')).attrs['for'], 'tile-example')
        links = [node.attrs['href'] for node in doc.walk('a')]
        self.assertIn('https://example.com/', links)

if __name__ == '__main__':
    unittest.main()
