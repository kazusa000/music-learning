"""Audit prepared Ample banks without trusting historical sample hashes.

Reuse the resumable stable-file auditor, then publish a bank-relative manifest
for the existing admission API. Samples are read in place, never copied.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from .ample_publication import PRODUCTS
from .installed_libraries import verify_installed_library
from .kontakt_resource_audit import (
    _encoded, _plain_path, _publish_manifest, _relative, _signature,
    audit_prepared_library,
)


def preparation_for_audit(journal, inventory, product, *, allowed_library_root):
    """Bind every sample and index to the original, explicit preparation."""
    spec = PRODUCTS[product]
    allowed, _ = _plain_path(allowed_library_root, directory=True)
    destination, _ = _plain_path(allowed / spec['bank_root'], directory=True)
    if (journal.get('instrument') != product or journal.get('library_id') != spec['library_id']
            or journal.get('destination') != str(destination)
            or journal.get('catalog_written') is not False
            or journal.get('status') != 'resources_prepared_pending_full_hash_and_audio_validation'):
        raise ValueError('Ample preparation identity or status differs')
    prior = inventory['libraries'][product]['ascf']
    source = journal.get('source')
    if not isinstance(source, str):
        raise ValueError('Ample preparation source is missing')
    samples = journal.get('hardlinks')
    if not isinstance(samples, list) or len(samples) != len(prior) or not samples:
        raise ValueError('Ample preparation sample coverage differs')
    names = [row.get('name') for row in prior]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate original Ample sample')
    original = {row['name']: row for row in prior}
    files, seen = [], set()
    for row in samples:
        name = _relative(row['name'], 'Ample sample')
        if '/' in name or not name.startswith(product) or not name.endswith('.ascf') or name in seen:
            raise ValueError('Invalid or duplicate Ample sample')
        expected = original.get(name)
        if (expected is None or row.get('bytes') != expected['bytes']
                or row.get('prior_inventory_sha256') != expected['sha256']):
            raise ValueError('Ample sample differs from original inventory')
        seen.add(name)
        files.append(dict(path=name, source=name, bytes=row['bytes'],
                          transfer='hardlink', sha256=expected['sha256']))
    if seen != set(original):
        raise ValueError('Ample preparation omits original samples')
    indexes = journal.get('indexes')
    if not isinstance(indexes, list) or not indexes:
        raise ValueError('Ample preparation lacks sample indexes')
    for row in indexes:
        name = _relative(row['name'], 'Ample index')
        original_name = _relative(row['source'], 'Ample source index')
        if ('/' in name or '/' in original_name or not name.startswith(product)
                or not name.endswith('_DBConfig.cf') or name in seen):
            raise ValueError('Invalid or duplicate Ample index')
        seen.add(name)
        files.append(dict(path=name, source=original_name, bytes=row['bytes'],
                          transfer='independent_copy', sha256=row['sha256']))
    return dict(status='resources_prepared_pending_fresh_hash_and_plugin_validation',
                catalog_written=False, source=source, destination=str(destination), files=files)


def audit_bank(journal, inventory, product, checkpoint, *, inventory_sha256,
               allowed_library_root=Path(r'H:\music\Sound Library'), budget_probe=None,
               budget_chunk_bytes=64 * 1024**2):
    """Fresh audit plus content-addressed, product-relative admission reference."""
    if journal.get('source_inventory_sha256') != inventory_sha256:
        raise ValueError('Ample preparation inventory digest differs')
    preparation = preparation_for_audit(journal, inventory, product,
                                        allowed_library_root=allowed_library_root)
    spec = PRODUCTS[product]
    destination = Path(preparation['destination'])
    common = destination.parent
    result = audit_prepared_library(
        destination, preparation, checkpoint, library_id='ample-resource-' + product,
        source_manifest_sha256=inventory_sha256, allowed_library_root=allowed_library_root,
        bank_root=common, budget_probe=budget_probe, budget_chunk_bytes=budget_chunk_bytes,
    )
    if result['status'] != 'audited':
        return result
    # Every output row is bound to its still-current opened-file signature;
    # prefix removal changes the reference root, not any asserted file bytes.
    prefix = destination.name + '/'
    rows = []
    for row in result['prepared_files']:
        path = row['path']
        if not path.startswith(prefix):
            raise ValueError('Ample audit file escaped its product bank')
        relative = _relative(path[len(prefix):], 'Ample bank manifest file')
        target, info = _plain_path(destination / Path(*PurePosixPath(relative).parts), directory=False)
        if _signature(info) != row['signature']:
            raise ValueError('Ample audited resource changed before manifest publication')
        rows.append(dict(path=relative, bytes=row['bytes'], sha256=row['sha256'], mtime_ns=info.st_mtime_ns))
    payload = _encoded(dict(schema_version=1, library_id=spec['library_id'], files=rows))
    digest = hashlib.sha256(payload).hexdigest()
    manifest = _publish_manifest(destination, payload, digest)
    reference = dict(library_id=spec['library_id'], bank_root=spec['bank_root'],
                     manifest_path='Manifests/' + digest + '.json', manifest_sha256=digest)
    verification = verify_installed_library(SimpleNamespace(instrument_library=allowed_library_root), reference)
    return dict(status='audited', product=product, bank_root=spec['bank_root'],
                installed_library=reference, source_manifest_sha256=inventory_sha256,
                prepared_file_count=len(rows), audited_total_bytes=sum(row['bytes'] for row in rows),
                manifest=dict(path=reference['manifest_path'], bytes=manifest.stat().st_size, sha256=digest),
                resource_audit_manifest=result['installed_library'],
                checkpoint_path=str(checkpoint), verification=verification,
                catalog_written=False, audio_verified=False)
