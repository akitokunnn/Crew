"""The same bundled resource interface is used by HTTP and native UI fixtures."""
import tests
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew.web_assets import AssetCatalog


class AssetCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'index.html').write_text('<link rel="stylesheet" href="/main.css"><script src="/feature.js"></script>', encoding='utf-8')
        (self.root / 'storage-history.html').write_text('<title>History</title>', encoding='utf-8')
        (self.root / 'main.css').write_text('body{}', encoding='utf-8')
        (self.root / 'feature.js').write_text('window.feature=true', encoding='utf-8')

    def test_new_module_assets_need_no_second_route_registration(self):
        (self.root / 'next-module.js').write_text('next()', encoding='utf-8')
        catalog = AssetCatalog(self.root)
        asset = catalog.resolve('/next-module.js')
        self.assertEqual(asset.read_bytes(), b'next()')
        self.assertEqual(asset.content_type, 'text/javascript; charset=utf-8')
        self.assertEqual(asset.cache_control, 'no-store')
        self.assertEqual(catalog.routes['/next-module.js'][0], 'next-module.js')

    def test_missing_entry_reference_is_a_real_error(self):
        (self.root / 'feature.js').unlink()
        with self.assertRaisesRegex(ValueError, 'index.html.*feature.js'):
            AssetCatalog(self.root).validate()

    def test_history_entry_is_validated_and_selected_explicitly(self):
        (self.root / 'storage-history.html').write_text('<script src="/missing.js"></script>', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'storage-history.html.*missing.js'):
            AssetCatalog(self.root).validate()
        (self.root / 'storage-history.html').write_text('<title>History</title>', encoding='utf-8')
        catalog = AssetCatalog(self.root)
        self.assertIn(b'History', catalog.resolve('/', history=True).read_bytes())
        self.assertNotIn(b'History', catalog.resolve('/').read_bytes())
        self.assertIsNone(catalog.resolve('/storage-history.html'))

    def test_code_data_hidden_files_and_path_traversal_are_not_published(self):
        for name in ('secret.py', 'private.json', '.hidden.js', 'index.sql'):
            (self.root / name).write_text('private', encoding='utf-8')
        catalog = AssetCatalog(self.root)
        for path in ('/secret.py', '/private.json', '/.hidden.js', '/index.sql', '/../feature.js', '/%2e%2e/feature.js', '//feature.js'):
            with self.subTest(path=path):
                self.assertIsNone(catalog.resolve(path))

    def test_source_selection_is_not_the_importing_python_module_directory(self):
        first = AssetCatalog(self.root)
        alternate = self.root / 'alternate'
        alternate.mkdir()
        for name in ('index.html', 'storage-history.html', 'main.css', 'feature.js'):
            (alternate / name).write_bytes((self.root / name).read_bytes())
        (alternate / 'feature.js').write_text('alternate()', encoding='utf-8')
        self.assertEqual(first.resolve('/feature.js').read_bytes(), b'window.feature=true')
        self.assertEqual(AssetCatalog(alternate).resolve('/feature.js').read_bytes(), b'alternate()')

    def test_fonts_and_logo_keep_their_cache_policy(self):
        (self.root / 'fonts').mkdir()
        (self.root / 'fonts' / 'demo.woff2').write_bytes(b'font')
        (self.root / 'logo.svg').write_text('<svg/>', encoding='utf-8')
        catalog = AssetCatalog(self.root)
        self.assertEqual(catalog.resolve('/fonts/demo.woff2').content_type, 'font/woff2')
        self.assertEqual(catalog.resolve('/fonts/demo.woff2').cache_control, 'public, max-age=604800')
        self.assertEqual(catalog.resolve('/logo.svg').cache_control, 'public, max-age=604800')

    def test_symlink_assets_are_rejected(self):
        target = self.root / 'private.txt'
        target.write_text('not public', encoding='utf-8')
        try:
            (self.root / 'linked.js').symlink_to(target)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            AssetCatalog(self.root).validate()

    def test_link_detection_rejects_an_asset_without_requiring_os_privileges(self):
        original = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda path: path.name == 'feature.js' or original(path)):
            with self.assertRaisesRegex(ValueError, 'symbolic links.*feature.js'):
                AssetCatalog(self.root).validate()

    def test_unknown_application_route_does_not_load_frontend_files(self):
        self.assertIsNone(AssetCatalog(self.root / 'headless-api').resolve('/api/unknown'))

    def test_real_frontend_entries_are_complete(self):
        root = Path(__file__).resolve().parents[1] / 'crew' / 'web'
        catalog = AssetCatalog(root)
        catalog.validate()
        self.assertIsNotNone(catalog.resolve('/app.js'))
        self.assertIsNotNone(catalog.resolve('/right-panels.css'))
