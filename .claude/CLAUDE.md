## Architecture

Two components work together. Add-on exists because claude-agent-sdk can't be installed on the HAOS host.

### Integration (HACS custom component)

- Registers as a HA conversation agent via `ConversationEntity`
- Builds system prompts with exposed entities and HA context
- Manages session mapping (conversation_id -> session_id) in a bounded LRU cache
- Delegates query execution to add-on

### Add-on (Docker container)

- Add-ons role is to simply wrap sdk in a HTTP api.
- MCP tools proxy HA service calls REST API using `SUPERVISOR_TOKEN`
