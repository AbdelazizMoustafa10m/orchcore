# Changelog

## [2.0.1](https://github.com/AbdelazizMoustafa10m/orchcore/compare/v2.0.0...v2.0.1) (2026-07-02)


### CI/CD

* give gh workflow run a repo in the release job ([75c04f2](https://github.com/AbdelazizMoustafa10m/orchcore/commit/75c04f24c2956b56b44959293f141078a11a5ea7))
* make release pipeline self-triggering without a PAT ([5137fb0](https://github.com/AbdelazizMoustafa10m/orchcore/commit/5137fb06508638a0c588ddd4a3334729559e2447))

## [2.0.0](https://github.com/AbdelazizMoustafa10m/orchcore/compare/v1.0.0...v2.0.0) (2026-07-02)


### ⚠ BREAKING CHANGES

* with both a flag profile and a ToolSet configured, ToolSet-domain flags inside the profile are no longer emitted at all (they are dropped with a warning); malformed flag_profile values raise instead of being passed through.
* orchcore.registry.AgentMode is removed. Migrate flags={AgentMode.PLAN: [...]} to flags={"plan": [...]}, and the mode= parameter of run_pipeline/run_phase/run_parallel/AgentRunner.run to flag_profile=. Calls relying on the implicit PLAN default must pass flag_profile="plan" explicitly. TOML registry files need no changes. A ToolSet no longer suppresses profile flags: keep tool-restriction flags in ToolSets only, or they will be emitted alongside the translation.

### Features

* replace AgentMode enum with consumer-defined flag profiles ([05d001d](https://github.com/AbdelazizMoustafa10m/orchcore/commit/05d001de510ab782f736cb5eae6ef4a375bacaaa))


### Bug Fixes

* consume variadic tool-flag values when stripping profiles ([7431649](https://github.com/AbdelazizMoustafa10m/orchcore/commit/7431649989ff5fa657d5548e360a328250a4b49f))
* strip clap attached short-option values in managed-flag filter ([db45f28](https://github.com/AbdelazizMoustafa10m/orchcore/commit/db45f283f8c001fa0047a5e40058e70026088659))
* strip ToolSet-managed flags from profiles and validate profile names ([26fb935](https://github.com/AbdelazizMoustafa10m/orchcore/commit/26fb935c675deac7ebefc1312a91d26743425bf0))


### Build System

* bump idna from 3.11 to 3.15 ([#17](https://github.com/AbdelazizMoustafa10m/orchcore/issues/17)) ([3a9eccc](https://github.com/AbdelazizMoustafa10m/orchcore/commit/3a9eccc07d13302dc3b2eb1a0b657eb39798801c))
* bump pygments from 2.19.2 to 2.20.0 ([#7](https://github.com/AbdelazizMoustafa10m/orchcore/issues/7)) ([89c53a0](https://github.com/AbdelazizMoustafa10m/orchcore/commit/89c53a01ee7fb1a634089ff6a6becf6371cc42eb))
* bump pytest from 9.0.2 to 9.0.3 ([#13](https://github.com/AbdelazizMoustafa10m/orchcore/issues/13)) ([bbcd21c](https://github.com/AbdelazizMoustafa10m/orchcore/commit/bbcd21c9700e235156079940cba888bf4e6bfe0d))
* bump urllib3 from 2.6.3 to 2.7.0 ([#16](https://github.com/AbdelazizMoustafa10m/orchcore/issues/16)) ([ead6811](https://github.com/AbdelazizMoustafa10m/orchcore/commit/ead68110d9dfc416838bf50ad828fa0bf67e41b4))


### CI/CD

* bump the github-actions group across 1 directory with 12 updates ([#14](https://github.com/AbdelazizMoustafa10m/orchcore/issues/14)) ([8e24477](https://github.com/AbdelazizMoustafa10m/orchcore/commit/8e244772be6b04d91d17cf71fe1ca1821341bd01))

## [1.0.0](https://github.com/AbdelazizMoustafa10m/orchcore/compare/v0.2.1...v1.0.0) (2026-06-10)


### Breaking Changes

* harden public contracts, keyword-only constructors, immutable nested configuration models, stream parsing, and pipeline success semantics for the 1.0 release ([3317191](https://github.com/AbdelazizMoustafa10m/orchcore/commit/3317191), [d4fde59](https://github.com/AbdelazizMoustafa10m/orchcore/commit/d4fde59))


### Features

* harden dynamic workflow execution with safer subprocess boundaries, explicit working directories, retry recovery controls, and path containment ([4eeda32](https://github.com/AbdelazizMoustafa10m/orchcore/commit/4eeda32))
* implement the phase 0 safety baseline across prompt loading, workspace handling, signal handling, and regression coverage ([e6b7f38](https://github.com/AbdelazizMoustafa10m/orchcore/commit/e6b7f38))
* restructure stream parsing into format-specific Pydantic wire parsers with golden fixtures for Claude, Codex, Copilot, Gemini, and OpenCode ([d4fde59](https://github.com/AbdelazizMoustafa10m/orchcore/commit/d4fde59))
* add advisory agent CLI version compatibility checks and typed registry support for known-good and known-bad versions ([3317191](https://github.com/AbdelazizMoustafa10m/orchcore/commit/3317191))


### Bug Fixes

* harden runner and registry behavior with filtered environments, stdin prompt transport, process-tree cleanup, typed error categories, bounded output buffering, and dist smoke gates ([d1f9558](https://github.com/AbdelazizMoustafa10m/orchcore/commit/d1f9558))


### CI/CD

* harden release gates with Python 3.12/3.13 linting, strict warnings, docs builds, coverage thresholds, README example checks, and wheel/sdist smoke tests ([e9acefa](https://github.com/AbdelazizMoustafa10m/orchcore/commit/e9acefa))


### Documentation

* refresh implementation documentation, ADRs, quickstart examples, registry guidance, flow control docs, and stream-event reference material for the 1.0 surface ([8e2a9b3](https://github.com/AbdelazizMoustafa10m/orchcore/commit/8e2a9b3))

## [0.2.1](https://github.com/AbdelazizMoustafa10m/orchcore/compare/v0.2.0...v0.2.1) (2026-03-29)


### Documentation

* add architecture diagram to README ([169f582](https://github.com/AbdelazizMoustafa10m/orchcore/commit/169f582ab5f8f218e41dbfd760cbb8d793a8aa98))

## [0.2.0](https://github.com/AbdelazizMoustafa10m/orchcore/compare/v0.1.0...v0.2.0) (2026-03-29)


### Features

* add Zensical docs site with GitHub Pages deployment ([832d5f4](https://github.com/AbdelazizMoustafa10m/orchcore/commit/832d5f474d89d73367fc40e8cf3f88a14f0064d6))

## [0.1.0](https://github.com/AbdelazizMoustafa10m/orchcore/compare/v0.0.1...v0.1.0) (2026-03-28)


### Features

* add phase 3 orchestration modules ([ad9cff0](https://github.com/AbdelazizMoustafa10m/orchcore/commit/ad9cff0f38e52dadf6eecc2cf5cea09a30dcbc39))
* add phase 4 recovery and telemetry ([0a7e646](https://github.com/AbdelazizMoustafa10m/orchcore/commit/0a7e64618b4947591dab82fc24611a3bac8adf0a))
* add stream processing pipeline with full test coverage (Phase 1) ([68ea98f](https://github.com/AbdelazizMoustafa10m/orchcore/commit/68ea98fb09e94b230647d34bfb0ec5546fc3f7e6))
* add telemetry extra dependencies ([1472297](https://github.com/AbdelazizMoustafa10m/orchcore/commit/147229700c44df204e33bd44e950f028220f79be))
* implement phase 2 orchestration modules ([656c26a](https://github.com/AbdelazizMoustafa10m/orchcore/commit/656c26ab22bccac0d1c3cadc587d29ab2d2086d0))
* scaffold orchcore project structure (Phase 0) ([c15bc49](https://github.com/AbdelazizMoustafa10m/orchcore/commit/c15bc49e80e3ed1f034e5659964da37224965e04))


### Bug Fixes

* add explicit type annotations to reach 100% pyright verifytypes score ([8f8cead](https://github.com/AbdelazizMoustafa10m/orchcore/commit/8f8cead52d083fa9a7144e29d34ddaead3cfa44c))
* add py.typed marker and use uv in Makefile ([ee6b0d0](https://github.com/AbdelazizMoustafa10m/orchcore/commit/ee6b0d0feed3123f873e5bc72c50453e33360f53))
* **ci:** install dev extras in CI workflow ([4c95321](https://github.com/AbdelazizMoustafa10m/orchcore/commit/4c95321894068cee83bce9ce7e6a3b585666ef7a))
* **ci:** update codeql-action to v3.35.1 in scorecards workflow ([38395fe](https://github.com/AbdelazizMoustafa10m/orchcore/commit/38395fef73fa2b57807d6edbc5982b35a8b583eb))
* resolve 10 audit findings across stream, pipeline, workspace, and registry modules ([2f5789b](https://github.com/AbdelazizMoustafa10m/orchcore/commit/2f5789b827650e6e91fdaad5a4898ec1e4947978))
* resolve 15 audit findings across pipeline, stream, runner, and config modules ([5ac8c08](https://github.com/AbdelazizMoustafa10m/orchcore/commit/5ac8c0807156c045c03eaf241d9018f10843019c))
* resolve 21 audit findings across source and test modules ([2729424](https://github.com/AbdelazizMoustafa10m/orchcore/commit/272942401e5db72502150e472398439c5e6085b2))
* resolve 50 audit findings across all orchcore modules ([aca114d](https://github.com/AbdelazizMoustafa10m/orchcore/commit/aca114dc8d3072f67ffeebf3a4996df4efc2aa78))
* **tests:** make integration and template tests cross-platform ([5e83ae7](https://github.com/AbdelazizMoustafa10m/orchcore/commit/5e83ae72686c152d03c37497aa0c6d9e0c22c602))


### Documentation

* add CI, release, and PyPI badges to README ([0da542c](https://github.com/AbdelazizMoustafa10m/orchcore/commit/0da542cff0227d46d3f14f920b32687ce4567ac7))
* add project documentation site and lean README ([09626eb](https://github.com/AbdelazizMoustafa10m/orchcore/commit/09626eb3c08c58d5d30a8e22697b17de7d1906e7))
* fix 10 audit findings and add 5 missing module guides ([c09a74f](https://github.com/AbdelazizMoustafa10m/orchcore/commit/c09a74fbb4ac02e35c9dd06352734ee61a42aa80))
* fix 16 remaining documentation inaccuracies across doc/ and docs/ ([147fa5a](https://github.com/AbdelazizMoustafa10m/orchcore/commit/147fa5a17c1444c3a4a7b659ed4afbcc3d3a8d99))
* fix 5 remaining inaccuracies in guides ([af52fe9](https://github.com/AbdelazizMoustafa10m/orchcore/commit/af52fe95858d89bfe0d626d8830397fc2a681050))
* fix SignalManager description to match implementation ([c536532](https://github.com/AbdelazizMoustafa10m/orchcore/commit/c536532ca3a9f1a7f8564af6a72039edfebfec74))
* sync stream parser documentation ([e8991d1](https://github.com/AbdelazizMoustafa10m/orchcore/commit/e8991d131802d13d7e37848ec245408b93e33fee))
* sync telemetry extra guidance ([0aa047e](https://github.com/AbdelazizMoustafa10m/orchcore/commit/0aa047e2a032d87ac8ec61ce4149030c4c85bdb3))


### Build System

* add pyright verifytypes for public API type completeness checks ([400f031](https://github.com/AbdelazizMoustafa10m/orchcore/commit/400f03198118fab7cbcc735d9e98188b01cd2fa3))
* switch to git-tag-based versioning via hatch-vcs ([221bf9d](https://github.com/AbdelazizMoustafa10m/orchcore/commit/221bf9d91b1b0a5197d2ff45610597299ef13ce7))


### CI/CD

* add automated releases with release-please and commit linting ([8ebfb7d](https://github.com/AbdelazizMoustafa10m/orchcore/commit/8ebfb7dc1b1c1812e25735421382f715132e50b1))
* add automatic GitHub Release creation on publish ([632d88d](https://github.com/AbdelazizMoustafa10m/orchcore/commit/632d88dc66fe11730a8578dc401c0655acfdbb25))
* add GitHub Actions workflows for CI and PyPI publishing ([60648de](https://github.com/AbdelazizMoustafa10m/orchcore/commit/60648de1c648805acca08641c45c1eec0398ebdd))
* remove unnecessary Test PyPI workflow ([df2894a](https://github.com/AbdelazizMoustafa10m/orchcore/commit/df2894a1b17f5e3b91210051ab804b32b3a690a9))


### Tests

* add Hypothesis coverage for stream parsers ([5809e97](https://github.com/AbdelazizMoustafa10m/orchcore/commit/5809e976342792242e6b3db60c14f9bce2fc8874))

## Changelog

All notable changes to this project will be documented in this file.

This changelog is automatically generated by
[release-please](https://github.com/googleapis/release-please) from
[conventional commits](https://www.conventionalcommits.org/).
