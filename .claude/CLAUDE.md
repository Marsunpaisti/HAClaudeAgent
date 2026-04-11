## Architecture

Two components that work together. The add-on exists because claude-agent-sdk can't be installed on the HAOS host.

### Integration (HACS custom component)

- Registers as a HA conversation agent via `ConversationEntity`
- Builds system prompts with exposed entities and HA context
- Manages session mapping (conversation_id -> session_id) in a bounded LRU cache
- Delegates query execution to the add-on

### Add-on (Docker container)

- The add-ons role is to simply wrap claude-agent-sdk in a HTTP api.
- MCP tools proxy HA service calls via the REST API using `SUPERVISOR_TOKEN`
