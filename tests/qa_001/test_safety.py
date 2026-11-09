#!/usr/bin/env python3
"""Filesystem-negative tests in a disposable owned build subtree, no user files."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import os
import unittest
import sys

sys.dont_write_bytecode = True
import common


class OwnershipTests(unittest.TestCase):
    @contextmanager
    def workspace(self):
        common.require_build_root()
        common.BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='safety-', dir=common.BUILD) as temporary:
            previous = common.HERE, common.BUILD, common.ROOT
            common.HERE = Path(temporary)
            common.BUILD = Path(temporary) / 'build'
            common.ROOT = Path(temporary)
            try:
                yield common.HERE
            finally:
                common.HERE, common.BUILD, common.ROOT = previous

    def test_normal_owned_destination(self):
        with self.workspace():
            path = common.BUILD / 'inputs/test.mp4'
            self.assertEqual(common.require_owned_build(path), path)

    def test_build_symlink_cannot_redefine_trust_root(self):
        with self.workspace() as here:
            outside = here / 'outside'
            outside.mkdir()
            sentinel = outside / 'untouched'
            sentinel.write_text('preserve')
            common.BUILD.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                common.require_owned_build(common.BUILD / 'output.mp4')
            self.assertEqual(sentinel.read_text(), 'preserve')
            self.assertEqual(list(outside.iterdir()), [sentinel])

    def test_outward_parent_symlink_rejected(self):
        with self.workspace() as here:
            common.BUILD.mkdir()
            outside = here / 'outside'
            outside.mkdir()
            (common.BUILD / 'inputs').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                common.require_owned_build(common.BUILD / 'inputs/output.mp4')
            self.assertEqual(list(outside.iterdir()), [])

    def test_inward_parent_symlink_also_rejected(self):
        with self.workspace():
            common.BUILD.mkdir()
            (common.BUILD / 'real').mkdir()
            (common.BUILD / 'inputs').symlink_to(common.BUILD / 'real', target_is_directory=True)
            with self.assertRaises(ValueError):
                common.require_owned_build(common.BUILD / 'inputs/output.mp4')

    def test_leaf_symlink_rejected_without_touching_target(self):
        with self.workspace():
            common.BUILD.mkdir()
            target = common.BUILD / 'target.mp4'
            target.write_text('preserve')
            link = common.BUILD / 'output.mp4'
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                common.require_owned_build(link)
            self.assertEqual(target.read_text(), 'preserve')

    def test_parent_escape_rejected(self):
        with self.workspace() as here:
            with self.assertRaises(ValueError):
                common.require_owned_build(here / 'escape.mp4')

    def test_hardlink_to_source_is_rejected_before_truncation(self):
        with self.workspace():
            common.BUILD.mkdir()
            source = common.BUILD / 'source.mp4'
            source.write_bytes(b'preserve source bytes')
            alias = common.BUILD / 'output.partial.mp4'
            os.link(source, alias)
            with self.assertRaises(ValueError):
                common.require_owned_build(alias)
            self.assertEqual(source.read_bytes(), b'preserve source bytes')

    def test_special_output_node_is_rejected(self):
        with self.workspace():
            common.BUILD.mkdir()
            fifo = common.BUILD / 'output.partial.mp4'
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                common.require_owned_build(fifo)

    def test_evidence_hardlink_is_rejected_without_truncating_source(self):
        with self.workspace() as here:
            source = here / 'source-marker'
            source.write_bytes(b'preserve marker')
            evidence = here / 'evidence'
            evidence.mkdir()
            target = evidence / 'generation.json'
            os.link(source, target)
            with self.assertRaises(ValueError):
                common.write_json(target, {'test': 'must not write'})
            self.assertEqual(source.read_bytes(), b'preserve marker')

    def test_evidence_special_node_is_rejected(self):
        with self.workspace() as here:
            evidence = here / 'evidence'
            evidence.mkdir()
            target = evidence / 'generation.json'
            os.mkfifo(target)
            with self.assertRaises(ValueError):
                common.write_json(target, {'test': 'must not write'})

    def test_evidence_symlink_ancestor_is_rejected(self):
        with self.workspace() as here:
            real = here / 'real'
            real.mkdir()
            (here / 'evidence').symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                common.write_json(here / 'evidence/generation.json', {'test': 'must not write'})
            self.assertEqual(list(real.iterdir()), [])

    def test_sidecar_only_accepts_exact_owned_fixture_path(self):
        with self.workspace():
            expected = common.BUILD / 'inputs/sample.mp4'
            expected.parent.mkdir(parents=True)
            expected.write_bytes(b'owned fixture')
            rules = {'fixtures': {'sample': {'filename': 'sample.mp4'}}}
            valid = {'files': {'sample': {'path': 'build/inputs/sample.mp4'}}}
            self.assertEqual(common.owned_input_paths(rules, valid), {'sample': expected})
            for path in ['/private/tmp/user.mp4', '../user.mp4', 'build/outputs/sample.mp4']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    common.owned_input_paths(rules, {'files': {'sample': {'path': path}}})

    def test_sidecar_id_set_and_input_symlink_rejected(self):
        with self.workspace():
            rules = {'fixtures': {'sample': {'filename': 'sample.mp4'}}}
            with self.assertRaises(ValueError):
                common.owned_input_paths(rules, {'files': {}})
            expected = common.BUILD / 'inputs/sample.mp4'
            expected.parent.mkdir(parents=True)
            target = common.BUILD / 'target.mp4'
            target.write_bytes(b'preserve')
            expected.symlink_to(target)
            with self.assertRaises(ValueError):
                common.owned_input_paths(rules, {'files': {'sample': {'path': 'build/inputs/sample.mp4'}}})
            self.assertEqual(target.read_bytes(), b'preserve')

    def test_failed_processing_removes_partial_and_preserves_existing_final(self):
        with self.workspace():
            common.BUILD.mkdir()
            final = common.BUILD / 'output.mp4'
            final.write_bytes(b'previous verified result')
            partial = None
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                with common.temporary_output(final, 'negative-test') as path:
                    partial = path
                    path.write_bytes(b'incomplete output')
                    raise RuntimeError('injected failure')
            self.assertFalse(partial.exists())
            self.assertEqual(final.read_bytes(), b'previous verified result')

    def test_validated_result_atomically_replaces_final_and_leaves_no_partial(self):
        with self.workspace():
            final = common.BUILD / 'nested/output.mp4'
            with common.temporary_output(final, 'negative-test') as path:
                path.write_bytes(b'new verified result')
                path.replace(final)
            self.assertFalse(path.exists())
            self.assertEqual(final.read_bytes(), b'new verified result')


if __name__ == '__main__':
    unittest.main(verbosity=2)
