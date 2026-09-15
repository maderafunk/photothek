#!/usr/bin/env python3
"""Build an HTML/CSS photography wall. Network access happens only at build time."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parent
VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
NOW = lambda: datetime.now(timezone.utc).isoformat(timespec='seconds')

class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []
    def walk(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.walk(tag)
    def text(self):
        return ' '.join(c.text() if isinstance(c, Node) else c for c in self.children).strip()

class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)
    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent
    def handle_data(self, data):
        self.current.children.append(data)

def url(value, base=''):
    value = urljoin(base, unescape(str(value or '').strip()))
    p = urlsplit(value)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Expected a public HTTP(S) URL')
    if p.hostname in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Local URLs are not supported')
    return urlunsplit((p.scheme, p.netloc, quote(p.path, safe='/%:@!$&\'()*+,;=-._~'), p.query, ''))

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default

class Client:
    """Conditional requests, timeouts and byte/request caps; one client per source."""
    def __init__(self, site_id):
        self.path = ROOT / '.cache' / (site_id + '.json')
        self.cache = load_json(self.path, {})
        self.used = {}
    def get(self, address):
        address = url(address)
        if address in self.used:
            return self.used[address]
        if len(self.used) >= 8:
            raise ValueError('Per-source request limit reached')
        old = self.cache.get(address, {})
        headers = {'User-Agent': 'PhotographyWall/1.0 (static photography link gallery)', 'Accept': 'text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9'}
        if old.get('etag'):
            headers['If-None-Match'] = old['etag']
        if old.get('modified'):
            headers['If-Modified-Since'] = old['modified']
        try:
            with urlopen(Request(address, headers=headers), timeout=25) as response:
                raw = response.read(3_000_001)
                if len(raw) > 3_000_000:
                    raise ValueError('Source exceeds 3 MB limit')
                body = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                self.cache[address] = {'body': body, 'etag': response.headers.get('ETag'), 'modified': response.headers.get('Last-Modified')}
        except HTTPError as exc:
            if exc.code != 304 or 'body' not in old:
                raise
            body = old['body']
        self.used[address] = body
        return body
    def save(self):
        # Bound cache size to URLs used in this build; article bodies contain no image bytes.
        write_json(self.path, {k: self.cache[k] for k in self.used if k in self.cache})

def image(node, base, title='', article=''):
    attrs = node.attrs
    source = attrs.get('data-src') or attrs.get('data-original') or attrs.get('src')
    candidates = []
    # Split at descriptor separators, not commas embedded in CDN image URLs.
    for match in re.finditer(r'(\S+)\s+(\d+(?:\.\d+)?)(w|x)(?:\s*,\s*|$)', attrs.get('data-srcset') or attrs.get('srcset') or ''):
        candidate, size, unit = match.groups()
        candidates.append((float(size) * (370 if unit == 'x' else 1), candidate))
    if candidates:
        candidates.sort()
        source = next((v for w, v in candidates if w >= 600), candidates[-1][1])
    try:
        source = url(source, base) if source else ''
    except ValueError:
        return None
    if not source or re.search(r'(logo|avatar|tracking|pixel|icon|badge|button)', source, re.I):
        return None
    if urlsplit(source).path.lower().endswith('.svg'):
        return None
    for key in ('width', 'height'):
        if str(attrs.get(key, '')).isdigit() and int(attrs[key]) < 100:
            return None
    out = {'src': source, 'alt': attrs.get('alt') or title or 'Photograph', 'article': article or base, 'title': title}
    if all(str(attrs.get(k, '')).isdigit() and int(attrs[k]) > 0 for k in ('width', 'height')):
        out.update(width=int(attrs['width']), height=int(attrs['height']))
    return out

def unique(images, count):
    seen, result = set(), []
    for item in images:
        if item and item['src'] not in seen:
            seen.add(item['src']); result.append(item)
            if len(result) == count:
                break
    return result

def article_image(client, address, title):
    doc = Document(client.get(address)).root
    articles = list(doc.walk('article'))
    container = articles[0] if articles else doc
    # Prefer actual article photographs over mastheads and site social cards.
    found = unique((image(n, address, title, address) for n in container.walk('img')), 1)
    if found:
        return found[0]
    for n in doc.walk('meta'):
        if n.attrs.get('property') == 'og:image':
            return image(Node('img', {'src': n.attrs.get('content')}), address, title, address)

def feed_images(client, site):
    body = client.get(site['feed'])
    if '<!DOCTYPE' in body.upper():
        raise ValueError('Feed with a document type is unsupported')
    root = ET.fromstring(body)
    entries = root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
    if not entries:
        raise ValueError('No feed entries found')
    def fields(entry):
        return {n.tag.rsplit('}', 1)[-1]: n for n in entry}
    def dated(entry):
        f = fields(entry)
        for k in ('pubDate', 'published', 'updated'):
            if k in f:
                raw = f[k].text or ''
                try:
                    d = parsedate_to_datetime(raw) if k == 'pubDate' else datetime.fromisoformat(raw.replace('Z', '+00:00'))
                    return d.replace(tzinfo=d.tzinfo or timezone.utc).timestamp()
                except (ValueError, TypeError):
                    pass
        return 0
    entries.sort(key=dated, reverse=True)
    result = []
    for entry in entries[:site['images'] + 2]:
        f = fields(entry)
        title = f['title'].text or '' if 'title' in f else site['name']
        link = f.get('link')
        if link is None:
            continue
        address = url(link.get('href') or link.text, site['url'])
        if urlsplit(address).hostname != urlsplit(site['url']).hostname:
            continue
        selected = None
        for key in ('encoded', 'content', 'description', 'summary'):
            if key in f:
                html = f[key].text or ''
                if list(f[key]):
                    html += ''.join(ET.tostring(c, encoding='unicode') for c in f[key])
                photos = unique((image(n, address, title, address) for n in Document(html).root.walk('img')), 1)
                if photos:
                    selected = photos[0]; break
        if not selected and 'enclosure' in f and f['enclosure'].get('type', '').startswith('image/'):
            selected = image(Node('img', {'src': f['enclosure'].get('url')}), address, title, address)
        if not selected:
            selected = article_image(client, address, title)
        if selected:
            selected['published'] = next((f[k].text for k in ('pubDate', 'published', 'updated') if k in f), None)
            result.append(selected)
        if len(unique(result, site['images'])) == site['images']:
            break
    return unique(result, site['images'])

def discover(client, site):
    mode = site.get('mode', 'auto')
    if mode == 'feed' or mode == 'auto' and site.get('feed'):
        return feed_images(client, site)
    doc = Document(client.get(site['url'])).root
    if mode == 'auto':
        for n in doc.walk('link'):
            if n.attrs.get('type') in ('application/rss+xml', 'application/atom+xml'):
                return feed_images(client, {**site, 'feed': url(n.attrs['href'], site['url'])})
    result = []
    for n in doc.walk('img'):
        source = n.attrs.get('src', '')
        parent = n.parent
        while parent and parent.tag != 'a':
            parent = parent.parent
        article = url(parent.attrs.get('href'), site['url']) if parent and parent.attrs.get('href') else site['url']
        title = n.attrs.get('alt') or site['name']
        if mode == 'fototapeta' and not re.search(r'/20\d{2}/i/', '/' + source):
            continue
        if mode == 'fstop' and not re.search(r'cover\d+\.', source):
            continue
        if mode == 'lensculture':
            if 'recent-articles--cover-photo' not in n.attrs.get('class', '') or '/articles/' not in article:
                continue
            title = title.split(', Photography Competition')[0]
        found = image(n, site['url'], title, article)
        if found:
            # FOTOTAPETA often omits image alt text; retain its publisher label.
            if mode == 'lensculture':
                found['alt'] = title
            result.append(found)
    return unique(result, site['images'])

def config(path):
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get('sites'), list) or not value['sites']:
        raise ValueError('sites.yml must contain a nonempty sites list')
    ids = set()
    for s in value['sites']:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', str(s.get('id', ''))):
            raise ValueError('Each site needs a lowercase id, such as flakphoto')
        if s['id'] in ids:
            raise ValueError('Duplicate site id: ' + s['id'])
        ids.add(s['id'])
        if not isinstance(s.get('name'), str) or not s['name'].strip():
            raise ValueError('Each site needs a name')
        s['url'] = url(s['url'])
        if s.get('feed'):
            s['feed'] = url(s['feed'])
        s.setdefault('images', 3)
        if type(s['images']) is not int or not 1 <= s['images'] <= 6:
            raise ValueError('images must be an integer from 1 to 6')
        if s.get('mode', 'auto') not in ('auto', 'feed', 'fototapeta', 'fstop', 'lensculture'):
            raise ValueError('Unknown discovery mode')
        if s.get('mode') == 'feed' and not s.get('feed'):
            raise ValueError('feed mode requires a feed URL')
    return value

def render(cfg, records, output):
    tiles = []
    for idx, site in enumerate(cfg['sites']):
        record = records.get(site['id'], {})
        photos = record.get('images', [])[:site['images']]
        name, href = escape(site['name']), escape(url(site['url']), quote=True)
        if not photos:
            tiles.append(f'<article class="tile empty"><h2><a href="{href}">{name} ↗</a></h2><p>No photograph available yet.</p></article>')
            continue
        imgs = []
        for j, photo in enumerate(photos):
            dims = ''
            if photo.get('width') and photo.get('height'):
                dims = f' width="{int(photo["width"])}" height="{int(photo["height"])}"'
            loading = 'eager' if idx < 3 and j == 0 else 'lazy'
            imgs.append(f'<img src="{escape(url(photo["src"]), quote=True)}" alt="{escape(photo.get("alt") or site["name"], quote=True)}" loading="{loading}" decoding="async"{dims}>')
        klass = ' two' if len(photos) == 2 else ''
        tiles.append(f'<article class="tile"><a class="tile-link" href="{href}" aria-label="Visit {name}"><div class="pictures{klass}">{"".join(imgs)}</div><span class="caption"><span class="name">{name}</span><span class="visit" aria-hidden="true">↗</span></span></a></article>')
    links = ' '.join(f'<a href="{escape(s["url"], quote=True)}">{escape(s["name"])}</a>' for s in cfg['sites'])
    title = escape(str(cfg.get('title', 'Photography Wall')))
    output.mkdir(parents=True, exist_ok=True)
    content = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: http:; style-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="description" content="A photographic wall linking to independent photography publications and their recent work.">
<title>{title}</title><link rel="stylesheet" href="style.css"></head>
<body><a class="skip" href="#gallery">Skip to photographs</a>
<header><h1>{title}</h1><p class="edition">{len(cfg['sites']):02d} publications</p></header>
<main id="gallery" aria-label="Photography publications">{''.join(tiles)}</main>
<footer><p>Photographs belong to their respective creators.</p><nav aria-label="Publications">{links}</nav></footer>
</body></html>\n'''
    (output / 'index.html').write_text(content)
    css = ROOT / 'dist' / 'style.css'
    if output.resolve() != css.parent.resolve():
        (output / 'style.css').write_text(css.read_text())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Render the saved image selection without network requests')
    args = parser.parse_args()
    cfg = config(ROOT / 'sites.yml')
    path = ROOT / 'data' / 'gallery.json'
    old = load_json(path, {'sites': {}})['sites']
    def update(site):
        previous = old.get(site['id'], {})
        if previous.get('url') != site['url']:
            previous = {}
        if args.offline:
            return site['id'], previous
        client = Client(site['id'])
        try:
            photos = discover(client, site)
            if not photos:
                raise ValueError('No suitable images discovered')
            record = {'url': site['url'], 'checked_at': NOW(), 'updated_at': NOW(), 'images': photos, 'error': None}
            if photos == previous.get('images'):
                record['updated_at'] = previous.get('updated_at', record['updated_at'])
            print(f'{site["name"]}: {len(photos)} photographs', flush=True)
        except Exception as exc:
            record = {**previous, 'url': site['url'], 'checked_at': NOW(), 'error': str(exc)[:300]}
            print(f'WARNING {site["name"]}: {exc}; retaining {len(previous.get("images", []))} previous photographs', flush=True)
        finally:
            client.save()
        return site['id'], record
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = dict(pool.map(update, cfg['sites']))
    if not args.offline:
        write_json(path, {'sites': records})
    render(cfg, records, ROOT / 'dist')
    if not any(r.get('images') for r in records.values()):
        raise SystemExit('No current or saved images available; refusing an empty deployment.')
    print('Built dist/index.html — no browser JavaScript.')

if __name__ == '__main__':
    main()
