# CHANGELOG

<!-- version list -->

## v1.9.0 (2026-06-28)

### Bug Fixes

- **test**: Update open_notes test needles for pin-btn rename
  ([#65](https://github.com/jacquardlabs/viva/pull/65),
  [`697957c`](https://github.com/jacquardlabs/viva/commit/697957cd87e31afd3eb7f0db86045c4d23c834b3))

### Features

- **ui**: Replace keep-open checkbox with pin-note toggle button
  ([#65](https://github.com/jacquardlabs/viva/pull/65),
  [`697957c`](https://github.com/jacquardlabs/viva/commit/697957cd87e31afd3eb7f0db86045c4d23c834b3))


## v1.8.1 (2026-06-28)

### Bug Fixes

- **ui**: Realign line-anchor controls to the blueprint design system
  ([#45](https://github.com/jacquardlabs/viva/pull/45),
  [`37736cf`](https://github.com/jacquardlabs/viva/commit/37736cf65d8f2e61064703acfe75fc72d071ff5b))


## v1.8.0 (2026-06-28)

### Features

- Learned recurring critiques (#17) ([#25](https://github.com/jacquardlabs/viva/pull/25),
  [`0c393cb`](https://github.com/jacquardlabs/viva/commit/0c393cb2c07fdf01be51059eba3a1b855e81c37e))


## v1.7.0 (2026-06-28)

### Features

- Line anchors, open notes across rounds, confidence triage (#15, #16, #12)
  ([#24](https://github.com/jacquardlabs/viva/pull/24),
  [`6de093f`](https://github.com/jacquardlabs/viva/commit/6de093fe6d9070e807b319bebb6e4d5590755b7e))


## v1.6.0 (2026-06-28)

### Bug Fixes

- Token-boundary type inference (#13) and dash-prefixed diff lines
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

### Documentation

- Document pre-review producers in SKILL.md (#9, #10, #11, #13)
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

### Features

- Deep-link annotation anchors to conflicting sections
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Required-section checklist gating producer ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Round-to-round section diff on rewritten cards
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Section annotation producers + round-to-round diff (#9, #10, #11, #13, #14)
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Shared annotation-merge helper for producers ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Spec<->code drift producer (#11), existence checks
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))


## v1.5.0 (2026-06-27)

### Features

- Per-section annotation channel → card badges ([#22](https://github.com/jacquardlabs/viva/pull/22),
  [`533016a`](https://github.com/jacquardlabs/viva/commit/533016ae5fa26abeed6b010e5200ce5e43154a62))


## v1.4.0 (2026-06-27)

### Features

- Support Python 3.8+ and add CI test matrix ([#21](https://github.com/jacquardlabs/viva/pull/21),
  [`d8a116d`](https://github.com/jacquardlabs/viva/commit/d8a116dc948b1b38c45d422f6c5f1afd3367f1dc))


## v1.3.0 (2026-06-27)

### Code Style

- Align image-attachment UI with reticle/blueprint system
  ([#20](https://github.com/jacquardlabs/viva/pull/20),
  [`1ef9d80`](https://github.com/jacquardlabs/viva/commit/1ef9d80f20cc3a9e30142ea96962e8301540b192))

### Features

- Square buttons to reticle look, fix title-block overflow
  ([#20](https://github.com/jacquardlabs/viva/pull/20),
  [`1ef9d80`](https://github.com/jacquardlabs/viva/commit/1ef9d80f20cc3a9e30142ea96962e8301540b192))


## v1.2.0 (2026-06-27)

### Features

- Image attachments in review and Q&A feedback ([#18](https://github.com/jacquardlabs/viva/pull/18),
  [`9275844`](https://github.com/jacquardlabs/viva/commit/9275844ae40d1c031cbff78f8c0b7dac3c610688))


## v1.1.1 (2026-06-27)

### Performance Improvements

- Collapse viva startup to one round-trip, defer doc read
  ([#7](https://github.com/jacquardlabs/viva/pull/7),
  [`eac06f0`](https://github.com/jacquardlabs/viva/commit/eac06f08d251fff637c3d72ecafc37141dec72ac))


## v1.1.0 (2026-06-21)

### Bug Fixes

- 8 polish fixes across parser and server ([#6](https://github.com/jacquardlabs/viva/pull/6),
  [`f62aa3d`](https://github.com/jacquardlabs/viva/commit/f62aa3dad258354a465161c152b53a1981f6d237))

### Features

- Keyboard shortcuts, lazy markdown render, per-card skip
  ([#6](https://github.com/jacquardlabs/viva/pull/6),
  [`f62aa3d`](https://github.com/jacquardlabs/viva/commit/f62aa3dad258354a465161c152b53a1981f6d237))


## v1.0.2 (2026-06-21)

### Bug Fixes

- 8 polish fixes across parser and server ([#5](https://github.com/jacquardlabs/viva/pull/5),
  [`1572893`](https://github.com/jacquardlabs/viva/commit/1572893d3c670fa1e5e19ca56ac93261b810d2a7))


## v1.0.1 (2026-06-21)

### Continuous Integration

- Push-notify marketplace on release ([#3](https://github.com/jacquardlabs/viva/pull/3),
  [`638a832`](https://github.com/jacquardlabs/viva/commit/638a832ddcc5c90e52ba3efc9bc61963506dbb71))

### Documentation

- Rewrite README in Bryan's voice ([#2](https://github.com/jacquardlabs/viva/pull/2),
  [`83cfae0`](https://github.com/jacquardlabs/viva/commit/83cfae0f67a49f1bc8d8ae7c323991297a0ffe9a))

### Performance Improvements

- Replace LLM parsing with bundled section parser
  ([#4](https://github.com/jacquardlabs/viva/pull/4),
  [`e5e7d93`](https://github.com/jacquardlabs/viva/commit/e5e7d93cbf5ea201d92dba2eee49c1c173dad141))


## v1.0.0 (2026-06-20)

- Initial Release
