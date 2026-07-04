# O's flow V2 Decisions

## Locked Direction

- O's flow is a mechanism for ingesting, organizing, storing, and reusing business data.
- RAG is a supporting component, not the main definition of the product.
- Do not rebuild the system as a brand-new platform from scratch.
- Keep the Discord intake flow as a reference entrypoint.
- Treat LINE, Discord, and other chat platforms as first-class intake channels over time.
- Make SQLite the development/test source of truth for cases, documents, artifacts, and processing state.
- Make InsForge/PostgreSQL the production and sales backend target.
- Treat file storage and RAG output as replaceable adapters.
- Keep repository access separate from storage access.
- Make the codebase compatible with Docker and future Vercel-style container deployment.
- Keep O's flow Core independent from any one backend implementation.
- Treat Vercel as an optional deployment/runtime target, not a required dependency.
- Use React-admin as the preferred base for O's flow Admin.
- Treat Appsmith, Directus, Retool, and similar generic tools as prototype options only.

## Current Working Assumptions

- RAG is not yet implemented in the existing MVP.
- The first real milestone is business-data ingestion and ledgering, not vector search.
- MCP/search tools will come after the ledger and storage abstractions are in place.
- HermesAgent is the conversational control surface for instructions, automation, and retrieval.
- The system must support both operator-driven ingestion and automatic ingestion.

## Next Implementation Targets

- SQLite schema and repository layer
- Local storage adapter
- Ingestion service
- FastAPI bootstrap
- Dockerfile and runtime scripts
