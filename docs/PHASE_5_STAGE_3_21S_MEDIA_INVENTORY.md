# Stage 3.21S Media Inventory

## Scope and method

This is the pre-cleanup inventory of `media/productions` and the post-cleanup location/hash verification. Each record in [the machine-readable inventory](./PHASE_5_STAGE_3_21S_MEDIA_INVENTORY.json) includes the original relative path, byte size, extension, local and UTC modification times, SHA-256, run ID, retention class, current path, and reference audit.

The inventory contains 456 original files totaling 534,925,684 bytes (510.145 MiB) across seven production/validation runs. No symlinks were found. Hashes were verified again after quarantine; all 456 current files (including quarantined files at their new locations) match their inventory hashes.

Reference checks covered exact relative/absolute paths and distinctive unique basenames in application code, documentation, production manifests/reports, and database text columns. The inventory document itself was excluded from dependency matching. Database checks used read-only access. Per-file findings are recorded in the JSON; no candidate file had an external database, application, manifest, or report dependency.

## Production inventory

| Run | Files | Bytes | MiB |
|---|---:|---:|---:|
| `val_3_21i_20261111_110449` | 87 | 94,881,600 | 90.486 |
| `val_3_21j_20261111_120906` | 105 | 51,848,489 | 49.448 |
| `val_3_21l_20261111_134222` | 3 | 10,023 | 0.010 |
| `val_3_21l_20261111_134455` | 88 | 35,389,428 | 33.750 |
| `val_3_21l_20261111_135230` | 113 | 61,063,047 | 58.234 |
| `val_3_21q_black_hole_20261111_2138` | 36 | 42,178,139 | 40.224 |
| `val_3_21r_black_hole_20261111_2257` | 24 | 249,554,958 | 238.042 |

The `val_3_21r_black_hole_20261111_2257` workspace, source material, local model assets, contact sheet, manifests, final video, and canonical thumbnail were retained. No production source, font, model, or renderer dependency was removed.

## Retention classification

| Class | Files | Treatment |
|---|---:|---|
| PERMANENT | 58 | Provenance and reusable dependencies; retained in place. |
| ARCHIVE | 251 | Historical generated assets; retained with their runs. |
| REGENERATABLE | 92 | Beat MP4 intermediates and concat lists; moved together to reversible quarantine. |
| UNKNOWN | 55 | Role or reproducibility not established; untouched. |

There are 24 exact SHA-256 duplicate groups covering 51 paths. Identical hashes were reported, not used as grounds for removal.

## Quarantined candidates and references

Each selected `render_work` directory contained 22 beat MP4s plus `concat_list.txt`. The concat list refers to its own beat renders; the list and renders were moved together, preserving that internal relationship.

| Original directory | Files | Bytes |
|---|---:|---:|
| `media/productions/val_3_21i_20261111_110449/render_work` | 23 | 33,739,703 |
| `media/productions/val_3_21j_20261111_120906/render_work` | 23 | 16,179,769 |
| `media/productions/val_3_21l_20261111_134455/render_work` | 23 | 12,569,846 |
| `media/productions/val_3_21l_20261111_135230/render_work` | 23 | 22,351,106 |
| **Total** | **92** | **84,840,424** |

For each candidate, the production manifest, `assets` and `narration` directories, and a completed run video were present. The read-only audit found no external path or unique-basename references in database records, application code, other production manifests/reports, or documentation. Regeneration was not run because production runs were prohibited; the `REGENERATABLE` classification is based on the intermediate-render role and the required source inputs remaining present. The four run directories, progress records, and all non-`render_work` files remain.

The quarantine manifest is [here](../media/quarantine/stage_3_21s/20261112T011828+0530/quarantine_manifest.json). It records each original and quarantine path, byte size, SHA-256, creation time, and the explicit age-policy override. All 92 quarantined files were independently re-hashed after the move; there were no missing files or hash mismatches.

## Requested media directory measurements

| Directory | Initial files | Initial bytes | Current files | Current bytes |
|---|---:|---:|---:|---:|
| `media/productions` | 456 | 534,925,684 | 364 | 450,085,260 |
| `media/video` | 1 | 12,192,969 | 1 | 12,192,969 |
| `media/thumbnail` | 1 | 115,373 | 1 | 115,373 |
| `media/thumbnails` | 0 (absent) | 0 | 0 (absent) | 0 |
| `media/audio` | 1 | 345,168 | 1 | 345,168 |
| `media/subtitles` | 4 | 9,092 | 4 | 9,092 |

Quarantine currently contains 93 files totaling 84,878,860 bytes (the 92 media files plus the manifest). This is a same-volume move, not reclaimed disk space.
