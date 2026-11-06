# Evidence Locker reference index

A ready-made copy of the [dblp computer science bibliography](https://dblp.org), packaged
once a month for the **offline reference check** of Evidence Locker.

With it, the app can check the references of a paper **on your own computer**: no title,
author or anything else from your paper is sent anywhere. The app downloads the file
itself; you don't need to do anything in this repository.

## What is in a release

| File | What it is |
|---|---|
| `dblp-reference-index-<date>.sqlite.xz` | the index (about 0.5 GB compressed, 1.6 GB unpacked) |
| `manifest.json` | record count, the SHA-256 of the DBLP dump it was built from, the SHA-256 and size of both files, and the index format |

Per publication the index keeps only: title, first author's surname, year, DOI, arXiv id
and the DBLP key.

## How the app checks a download

Before installing, the app checks that:
- the release was built with the same index format and the same title-normalization rules as
  the app (otherwise it asks you to update the app instead of giving wrong results);
- the compressed file has exactly the size and SHA-256 listed in `manifest.json`;
- the unpacked index has exactly the size, SHA-256 and number of publications listed there.

Only then does it replace the index you had. A failed or damaged download changes nothing.

## Prefer to build it yourself?

The app can build the same index on your computer straight from dblp.org (about 1.1 GB
download, 15-25 minutes). In the app's command line:

```
el-offline-admission build-reference-index --allow-network
```

## How releases are made

Once a month (on the 3rd) the workflow in `.github/workflows/` downloads the current
`dblp.xml.gz` from dblp.org, verifies DBLP's published MD5 checksum, builds the index with
the builder in `builder/`, checks that it is complete (at least 5 million publications,
well-known papers found) and publishes it here as a release.

`builder/` is an exact copy of the app's own index builder (title normalization, DBLP
reading, packaging); `builder/SOURCE.json` records which app version it was copied from and
the SHA-256 of every file. Standard library only:

```
python -I builder/build_release.py --dblp-dump dblp.xml.gz --work-dir work --out-dir release --notes notes.md
```

## Licence and attribution

The data comes from the **dblp computer science bibliography** (https://dblp.org), which is
released under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). The packaged
index files are released under CC0 1.0 as well; see `LICENSE`. We thank the dblp team at
Schloss Dagstuhl and the University of Trier.

This repository contains the index builder, the workflow and documentation; the index itself
is published as release files.
