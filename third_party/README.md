# Vendored build sources

This directory contains the exact source required to build the Ubuntu native
algorithm bridge after a normal NeuroBridge checkout. It deliberately contains
source only: no recording data, SDK Git metadata, credentials, caches or build
artifacts are included.

| Directory | Upstream revision | Purpose |
| --- | --- | --- |
| `AffectiveCloud-Algorithm-SDK` | `cd967c4f3d26ab1100c15b5e8c23604cc495dd23` | C++ algorithm package used by the bridge. Only its required `cpp/package` subtree is included. |
| `NumCpp` | `cd324a7f09ec1ec4ba2ef2d7bac4861c9a84ee47` | Header-only C++ dependency used while building the bridge. |

The authoritative repository URLs, commits and versions remain in
[`sdk.lock`](../sdk.lock). Do not update these directories independently; update
the lock and vendored snapshot together on a controlled build machine.

The SDK snapshot retains four local portability patches: `<limits>` in
`DSPBCG.h`; `<cstdint>`, `<vector>` and `std::uint8_t` in `Affective.h`;
`<cstring>` and `std::memset` in `FFTTool.cpp`; and the required standard
headers plus `std::strcmp` in `WaveletTool.cpp`. These patches remain necessary for standalone
headers and Linux/Windows compilation.

The behavioral patch `dual_eeg_waveform_limit_2000`, declared in `sdk.lock`,
changes the output clamp for both channels in `source/DP/EEG.cpp` from +/-500
to +/-2000. Values inside that interval are retained.
Filtering, quality grading, power calculations and two-decimal output rounding
are retained. All other package files match the locked upstream revision.
macOS, Linux and Windows bridge builds use this vendored snapshot so the local
patch is included on each platform.
