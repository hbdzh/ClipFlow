#!/usr/bin/env python3
"""Regression tests for pre-existing build/evidence path redirection."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('mac_build', HERE / 'build_spike.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BuildPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mac001-build-path-')
        self.root = Path(self.tmp.name) / 'task'
        self.outside = Path(self.tmp.name) / 'outside'
        self.root.mkdir()
        self.outside.mkdir()
        self.root = self.root.resolve()
        self.outside = self.outside.resolve()
        self.marker = self.outside / 'marker'
        self.marker.write_bytes(b'keep-original')

    def tearDown(self):
        self.assertEqual(self.marker.read_bytes(), b'keep-original')
        self.tmp.cleanup()

    def test_normal_nested_output_allowed(self):
        module.assert_safe_output(self.root, self.root / 'build/App.app/Contents/Info.plist')

    def test_build_root_symlink_rejected_before_write(self):
        (self.root / 'build').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_output(self.root, self.root / 'build/marker')

    def test_bundle_ancestor_symlink_rejected(self):
        (self.root / 'build').mkdir()
        (self.root / 'build/App.app').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_output(self.root, self.root / 'build/App.app/marker')

    def test_output_leaf_symlink_rejected(self):
        (self.root / 'result.json').symlink_to(self.marker)
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_output(self.root, self.root / 'result.json')

    def test_broken_symlink_rejected(self):
        (self.root / 'build').symlink_to(self.outside / 'missing', target_is_directory=True)
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_output(self.root, self.root / 'build/result')

    def test_hardlinked_output_rejected(self):
        os.link(self.marker, self.root / 'result.json')
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_output(self.root, self.root / 'result.json')

    def test_internal_cargo_style_hardlinks_allowed_by_tree_scan(self):
        (self.root / 'build').mkdir()
        first = self.root / 'build/first.o'
        first.write_bytes(b'internal-object')
        os.link(first, self.root / 'build/second.o')
        module.assert_safe_tree(self.root, self.root / 'build')

    def test_external_hardlink_in_tree_rejected(self):
        (self.root / 'build').mkdir()
        os.link(self.marker, self.root / 'build/escape.o')
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_tree(self.root, self.root / 'build')

    def test_outside_output_rejected(self):
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_output(self.root, self.marker)

    def test_existing_nested_alias_in_tree_rejected(self):
        (self.root / 'build/nested').mkdir(parents=True)
        (self.root / 'build/nested/escape').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(module.UnsafeBuildPath):
            module.assert_safe_tree(self.root, self.root / 'build')


if __name__ == '__main__':
    unittest.main()
