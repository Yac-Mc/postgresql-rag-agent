# Delta for Project Maintenance

## ADDED Requirements

### Requirement: Roadmap Uses Markdown Checkboxes

The README "Roadmap - Nuevas funcionalidades" section MUST track planned
features using markdown task-list checkboxes (`- [ ]` for pending, `- [x]`
for completed), instead of plain bullet text.

#### Scenario: Pending feature listed unchecked

- GIVEN `add-conversation-memory` has not been implemented yet
- WHEN a developer reads the README Roadmap section
- THEN the `add-conversation-memory` entry MUST appear as `- [ ] add-conversation-memory`

#### Scenario: Feature checked off at archive time

- GIVEN the `add-api-key-auth` change has been implemented and verified
- WHEN the change is archived
- THEN the README Roadmap entry for `add-api-key-auth` MUST be updated to
  `- [x] add-api-key-auth`
