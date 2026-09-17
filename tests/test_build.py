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
    def test_common_image_dimensions_are_read_from_headers(self):
        png = b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + (640).to_bytes(4, 'big') + (480).to_bytes(4, 'big')
        gif = b'GIF89a' + (320).to_bytes(2, 'little') + (240).to_bytes(2, 'little')
        self.assertEqual(build.image_dimensions(png), (640, 480))
        self.assertEqual(build.image_dimensions(gif), (320, 240))

    def test_minimal_config_generates_id_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sites.yml'
            path.write_text('sites:\n  - name: Example Magazine\n    link: https://example.com/\n')
            site = build.config(path)['sites'][0]
        self.assertEqual(site['id'], 'example-magazine')
        self.assertEqual(site['url'], 'https://example.com/')
        self.assertEqual(site['images'], 3)

    def test_one_image_count_applies_to_every_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sites.yml'
            path.write_text('images_per_site: 5\nsites:\n  - name: One\n    link: https://one.example/\n  - name: Two\n    link: https://two.example/\n')
            sites = build.config(path)['sites']
        self.assertEqual([site['images'] for site in sites], [5, 5])

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

    def test_header_images_and_resized_duplicates_are_excluded(self):
        markup = '''<header><img src="/hero.jpg" width="1200" height="300"></header>
        <main><img src="/work-400x300.jpg"><img src="/work-1200x900.jpg"></main>'''
        doc = build.Document(markup).root
        photos = build.unique((build.image(node, 'https://example.com/') for node in doc.walk('img')), 3)
        self.assertEqual([photo['src'] for photo in photos], ['https://example.com/work-400x300.jpg'])

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
        self.assertIn('Photography Wall is updated every Friday - last update:', html)
        self.assertNotIn('2026-09-17T', html)

    def test_render_orders_newest_updated_tile_first(self):
        older = {'id': 'older', 'url': 'https://older.example/', 'name': 'Older', 'images': 3}
        newer = {'id': 'newer', 'url': 'https://newer.example/', 'name': 'Newer', 'images': 3}
        records = {
            'older': {'updated_at': '2026-09-01T12:00:00+00:00', 'images': [{'src': 'https://older.example/photo.jpg'}]},
            'newer': {'updated_at': '2026-09-17T12:00:00+00:00', 'images': [{'src': 'https://newer.example/photo.jpg'}]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            build.render({'sites': [older, newer]}, records, Path(tmp), updated_on='2026-09-17')
            doc = build.Document((Path(tmp) / 'index.html').read_text()).root
        toggles = [node for node in doc.walk('input') if node.attrs.get('class') == 'detail-toggle']
        self.assertEqual([node.attrs['id'] for node in toggles], ['tile-newer-1', 'tile-older-1'])

    def test_render_orders_footer_links_alphabetically(self):
        zulu = {'id': 'zulu', 'url': 'https://zulu.example/', 'name': 'Zulu', 'images': 3}
        alpha = {'id': 'alpha', 'url': 'https://alpha.example/', 'name': 'alpha', 'images': 3}
        with tempfile.TemporaryDirectory() as tmp:
            build.render({'sites': [zulu, alpha]}, {}, Path(tmp), updated_on='2026-09-17')
            doc = build.Document((Path(tmp) / 'index.html').read_text()).root
        nav = next(doc.walk('nav'))
        self.assertEqual([node.text() for node in nav.walk('a')], ['alpha', 'Zulu'])

    def test_image_tiles_share_magazine_selection(self):
        site = {'id': 'example', 'url': 'https://example.com/', 'name': 'Example', 'images': 3}
        records = {'example': {'images': [
            {'src': 'https://example.com/one.jpg'},
            {'src': 'https://example.com/two.jpg'},
        ]}}
        with tempfile.TemporaryDirectory() as tmp:
            build.render({'sites': [site]}, records, Path(tmp), updated_on='2026-09-17')
            html = (Path(tmp) / 'index.html').read_text()
            doc = build.Document(html).root
        toggles = [node for node in doc.walk('input') if node.attrs.get('class') == 'detail-toggle']
        self.assertEqual(len(toggles), 2)
        self.assertEqual([node.attrs['type'] for node in toggles], ['radio', 'radio'])
        self.assertEqual([node.attrs['id'] for node in toggles], ['tile-example-1', 'tile-example-2'])
        self.assertTrue(all(node.attrs['name'] == 'selected-card' for node in toggles))
        self.assertEqual(len(list(doc.walk('article'))), 2)
        close_labels = [node for node in doc.walk('label') if node.attrs.get('class') == 'card-close']
        self.assertTrue(all(node.attrs['for'] == 'cards-closed' for node in close_labels))
        link = next(node for node in doc.walk('a') if node.attrs.get('href') == 'https://example.com/')
        self.assertEqual(link.attrs['target'], '_blank')
        self.assertEqual(link.attrs['rel'], 'noopener noreferrer')
        arrow = next(doc.walk('svg'))
        self.assertEqual(arrow.attrs['class'], 'external-arrow')
        backs = [node for node in doc.walk() if 'back-picture' in node.attrs.get('class', '').split()]
        self.assertEqual(len(backs), 2)
        self.assertTrue(all(len(list(node.walk('img'))) == 1 for node in backs))
        self.assertNotIn('↗', html)

if __name__ == '__main__':
    unittest.main()
