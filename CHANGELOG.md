# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.2.0] - 2026-02-11
### Added
- **Automated Testing Suite**: Introduced GitHub Actions CI workflow to run tests on every push and PR, ensuring stability (e.g., deduplication logic).
- CLI and interactive testing modes for easier development.
- Deduplication logic to prevent spamming the same alert.

### Changed
- **Alert Clarity**: Removed redundant quoted text from Cake Radar alerts, relying on Slack's native link previews for a cleaner look.
- Refactored configuration into `config.py` and keywords into `keywords.json`.

## [4.1.0] - 2026-02-03
### Fixed
- **Duplicate Posts**: Prevented bot from spamming duplicate alerts during slow connections.
- **Empty Messages**: Ensured every 'Cake Alert' includes the original message text.

## [4.0.0] - 2025-12-08
### Changed
- **AI Model Upgrade**: Upgraded from GPT-4o mini to GPT-5.1 for more accurate classification results.

## [3.2.1] - 2025-09-04
### Added
- Added "Pastel de nata" to treat keywords.

## [3.2.0] - 2024-12-10
### Changed
- **False Positive Filtering**: Increased minimum required confidence threshold to 85%.

## [3.1.0] - 2024-12-04
### Changed
- Excluded `#cake-radar` channel from triggering Cake Alerts.

## [3.0.0] - 2024-11-11
### Changed
- **Deployment**: Moved away from Zapier and deployed via Render.

### Added
- Added more treat keywords (e.g., "banana bread", "cupcake", "baklava").
- Added celebration-related keywords (e.g., "holiday", "anniversary", "vacation").

## [2.1.0] - 2024-10-19
### Added
- Added AI confidence level display (%) to notifications.

### Changed
- Adjusted AI confidence threshold to 75%.

## [2.0.0] - 2024-10-17
### Added
- **New Feature: Cake Radar AI**:
    - Integrated ChatGPT API to intelligently classify messages.
    - Added hybrid approach: Check keywords first, then verify with AI.

## [1.4.0] - 2024-10-17
### Changed
- Added channel exclusions for names containing "fca" and "slot".

## [1.3.0] - 2024-10-15
### Changed
- Excluded `#cake-radar` channel from triggering Cake Alerts.

## [1.2.0] - 2024-10-15
### Added
- New keywords: "candy", "candies".

## [1.1.0] - 2024-10-15
### Changed
- Excluded channels with keywords: "assortment", "asst", "recipes", "dist", "page".

## [1.0.0] - 2024-10-14
### Added
- Initial release!
