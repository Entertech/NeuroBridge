# Vendored build sources

This directory contains the exact source required to build the Ubuntu native
algorithm bridge after a normal NeuroBridge checkout. It deliberately contains
source only: no recording data, SDK Git metadata, credentials, caches or build
artifacts are included.

| Directory | Upstream revision | Purpose |
| --- | --- | --- |
| `AffectiveCloud-Algorithm-SDK` | `5623c5a1a43c6b04c3907d84ba2b9b86f9b010a2` | C++ algorithm package used by the bridge. Only its required `cpp/package` subtree is included. |
| `NumCpp` | `cd324a7f09ec1ec4ba2ef2d7bac4861c9a84ee47` | Header-only C++ dependency used while building the bridge. |

The authoritative repository URLs, commits and versions remain in
[`sdk.lock`](../sdk.lock). Do not update these directories independently; update
the lock and vendored snapshot together on a controlled build machine.
